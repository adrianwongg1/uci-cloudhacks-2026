import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr

AVIATIONSTACK_KEY = os.environ["AVIATIONSTACK_KEY"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")
table = dynamodb.Table(DYNAMODB_TABLE)

SYSTEM_PROMPT = (
    "You are a flight delay prediction system. You have deep knowledge of US "
    "domestic flight delay patterns based on historical data. Always return "
    "only a valid JSON object with no additional text, preamble, or markdown."
)

USER_PROMPT_TEMPLATE = """Predict the delay probability for this flight:
- Route: {origin} -> {destination}
- Airline: {airline}
- Scheduled departure: {dep_hour}:00 on {day_of_week}, {month}
- Distance: approximately {distance} miles

Return JSON only - no markdown, no extra text:
{{
  "delay_probability": 0.0 to 1.0,
  "risk_level": "LOW" or "MEDIUM" or "HIGH",
  "explanation": "one sentence explaining the main delay risk factor"
}}"""


def risk_label(p):
    if p <= 0.40:
        return "LOW"
    if p <= 0.65:
        return "MEDIUM"
    return "HIGH"


def send_sms(phone, message):
    sns.publish(PhoneNumber=phone, Message=message)


def call_bedrock(sub):
    try:
        dt = datetime.fromisoformat(sub["flight_date"] + "T" + sub["scheduled_departure"])
    except ValueError:
        dt = datetime.utcnow()
    features = {
        "origin": sub["origin"],
        "destination": sub["destination"],
        "airline": sub.get("airline", "Unknown"),
        "dep_hour": dt.hour,
        "day_of_week": dt.strftime("%A"),
        "month": dt.strftime("%B"),
        "distance": "unknown",
    }
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": USER_PROMPT_TEMPLATE.format(**features)}],
    }
    result = bedrock.invoke_model(modelId=BEDROCK_MODEL_ID, body=json.dumps(body))
    payload = json.loads(result["body"].read())
    return json.loads(payload["content"][0]["text"].strip())


def aviationstack_status(flight_iata, flight_date):
    qs = urllib.parse.urlencode(
        {"access_key": AVIATIONSTACK_KEY, "flight_iata": flight_iata, "flight_date": flight_date}
    )
    url = f"http://api.aviationstack.com/v1/flights?{qs}"
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
    flights = data.get("data") or []
    return flights[0] if flights else None


def update_sub(phone, flight_iata, updates):
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
    names = {f"#{k}": k for k in updates}
    values = {f":{k}": v for k, v in updates.items()}
    table.update_item(
        Key={"phone": phone, "flight_iata": flight_iata},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def handle_preflight(sub, days_until):
    try:
        prediction = call_bedrock(sub)
    except Exception as e:
        print(f"bedrock error for {sub['phone']}/{sub['flight_iata']}: {e}")
        return

    new_prob = float(prediction.get("delay_probability", 0))
    last = float(sub.get("last_predicted_risk") or 0)
    if abs(new_prob - last) <= 0.05:
        return

    pct = int(round(new_prob * 100))
    flight = sub["flight_iata"]
    if days_until >= 7:
        msg = (
            f"RouteWise: {days_until} days until {flight} ({sub['flight_date']}). "
            f"{sub['origin']}->{sub['destination']} {sub['scheduled_departure']}. "
            f"Current risk: {pct}% {risk_label(new_prob)}. We'll keep watching."
        )
    else:
        msg = (
            f"RouteWise: {days_until} days until {flight}. "
            f"{sub['origin']}->{sub['destination']} {sub['scheduled_departure']}. "
            f"Delay risk updated: {pct}% {risk_label(new_prob)}."
        )
    send_sms(sub["phone"], msg)
    update_sub(
        sub["phone"],
        sub["flight_iata"],
        {"last_predicted_risk": Decimal(str(new_prob))},
    )


def handle_day_of(sub):
    try:
        flight = aviationstack_status(sub["flight_iata"], sub["flight_date"])
    except Exception as e:
        print(f"aviationstack error for {sub['flight_iata']}: {e}")
        return
    if not flight:
        return

    status = (flight.get("flight_status") or "").lower()
    dep = flight.get("departure") or {}
    delay = dep.get("delay")
    delay_int = int(delay) if isinstance(delay, (int, float)) else 0
    new_dep_time = (dep.get("estimated") or dep.get("scheduled") or "")[-8:-3] or sub["scheduled_departure"]

    flight_id = sub["flight_iata"]
    dest = sub["destination"]
    last_delay = sub.get("last_delay_minutes")
    last_delay_int = int(last_delay) if last_delay is not None else None

    if status in ("landed", "arrived"):
        msg = (
            f"RouteWise: {flight_id} has landed at {dest}. "
            f"Final delay: +{delay_int} min. Safe travels! ✈️"
        )
        send_sms(sub["phone"], msg)
        update_sub(
            sub["phone"],
            sub["flight_iata"],
            {"status": "completed", "last_delay_minutes": delay_int},
        )
        return

    if delay_int == last_delay_int:
        return

    if last_delay_int in (None, 0) and delay_int > 0:
        msg = (
            f"RouteWise: {flight_id} is now delayed. New departure: {new_dep_time} "
            f"(+{delay_int} min). {sub['origin']}->{dest}."
        )
    elif delay_int == 0 and (last_delay_int or 0) > 0:
        msg = (
            f"RouteWise: {flight_id} delay cleared. Back on schedule - "
            f"departs {new_dep_time}."
        )
    elif delay_int > (last_delay_int or 0):
        msg = (
            f"RouteWise: {flight_id} delay updated - now +{delay_int} min. "
            f"New departure: {new_dep_time}."
        )
    else:
        msg = (
            f"RouteWise: {flight_id} delay reduced - now +{delay_int} min. "
            f"New departure: {new_dep_time}."
        )

    send_sms(sub["phone"], msg)
    update_sub(
        sub["phone"],
        sub["flight_iata"],
        {"last_delay_minutes": delay_int},
    )


def process(sub, now):
    try:
        flight_date = date.fromisoformat(sub["flight_date"])
    except ValueError:
        return
    days_until = (flight_date - now.date()).days
    hour = now.hour

    if days_until < 0:
        update_sub(sub["phone"], sub["flight_iata"], {"status": "completed"})
        return
    if days_until > 14:
        return
    if 3 <= days_until <= 14:
        if hour == 9 and now.minute < 15:
            handle_preflight(sub, days_until)
        return
    if 1 <= days_until <= 2:
        if hour in (8, 14, 20) and now.minute < 15:
            handle_preflight(sub, days_until)
        return
    if days_until == 0:
        handle_day_of(sub)


def handler(_event, _context):
    now = datetime.now(timezone.utc)

    subs = []
    scan_kwargs = {"FilterExpression": Attr("status").eq("active")}
    while True:
        resp = table.scan(**scan_kwargs)
        subs.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    for sub in subs:
        try:
            process(sub, now)
        except Exception as e:
            print(f"error processing {sub.get('phone')}/{sub.get('flight_iata')}: {e}")

    return {"processed": len(subs)}
