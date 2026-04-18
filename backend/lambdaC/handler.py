import json
import os
import re
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

USER_PROMPT = """Predict the delay probability for this flight:
- Route: {origin} -> {destination}
- Airline: {airline}
- Scheduled departure: {dep_hour:02d}:00 on {day_of_week}, {month}
- Estimated distance: {distance} miles

Return JSON only - no markdown, no extra text:
{{
  "delay_probability": 0.0 to 1.0,
  "risk_level": "LOW" or "MEDIUM" or "HIGH",
  "explanation": "one sentence explaining the main delay risk factor"
}}"""

JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def risk_label(p):
    if p <= 0.40:
        return "LOW"
    if p <= 0.65:
        return "MEDIUM"
    return "HIGH"


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def send_sms(phone, msg):
    sns.publish(PhoneNumber=phone, Message=msg)


def update_sub(phone, flight_iata, fields):
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
    table.update_item(
        Key={"phone": phone, "flight_iata": flight_iata},
        UpdateExpression=expr,
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
    )


def call_bedrock(sub):
    try:
        dt = datetime.fromisoformat(f"{sub['flight_date']}T{sub['scheduled_departure']}")
    except ValueError:
        dt = datetime.now(timezone.utc)
    feat = {
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
        "messages": [{"role": "user", "content": USER_PROMPT.format(**feat)}],
    }
    result = bedrock.invoke_model(modelId=BEDROCK_MODEL_ID, body=json.dumps(body))
    payload = json.loads(result["body"].read())
    text = JSON_FENCE.sub("", payload["content"][0]["text"]).strip()
    return json.loads(text)


def aviationstack_status(flight_iata, flight_date):
    qs = urllib.parse.urlencode({
        "access_key": AVIATIONSTACK_KEY,
        "flight_iata": flight_iata,
        "flight_date": flight_date,
    })
    with urllib.request.urlopen(
        f"http://api.aviationstack.com/v1/flights?{qs}", timeout=15
    ) as r:
        flights = (json.loads(r.read()).get("data") or [])
    return flights[0] if flights else None


def handle_preflight(sub, days_until):
    try:
        pred = call_bedrock(sub)
    except Exception as e:
        print(f"bedrock error {sub['phone']}/{sub['flight_iata']}: {e}")
        return

    new_prob = float(pred.get("delay_probability", 0))
    last = float(sub.get("last_predicted_risk") or 0)
    if abs(new_prob - last) <= 0.05:
        return

    pct = round(new_prob * 100)
    flight, origin, dest = sub["flight_iata"], sub["origin"], sub["destination"]
    sched = sub["scheduled_departure"]
    label = risk_label(new_prob)

    if days_until >= 7:
        msg = (
            f"RouteWise: {days_until} days until {flight} ({sub['flight_date']}). "
            f"{origin}->{dest} {sched}. Current risk: {pct}% {label}. We'll keep watching."
        )
    else:
        msg = (
            f"RouteWise: {days_until} days until {flight}. {origin}->{dest} {sched}. "
            f"Delay risk updated: {pct}% {label}."
        )
    send_sms(sub["phone"], msg)
    update_sub(sub["phone"], flight, {"last_predicted_risk": Decimal(str(new_prob))})


def extract_dep_time(flight, fallback):
    dep = flight.get("departure") or {}
    dt = parse_iso(dep.get("estimated") or dep.get("scheduled"))
    return dt.strftime("%H:%M") if dt else fallback


def handle_day_of(sub):
    try:
        flight = aviationstack_status(sub["flight_iata"], sub["flight_date"])
    except Exception as e:
        print(f"aviationstack error {sub['flight_iata']}: {e}")
        return
    if not flight:
        return

    status = (flight.get("flight_status") or "").lower()
    dep = flight.get("departure") or {}
    delay_raw = dep.get("delay")
    delay = int(delay_raw) if isinstance(delay_raw, (int, float)) else 0
    new_time = extract_dep_time(flight, sub["scheduled_departure"])

    flight_id, dest, origin = sub["flight_iata"], sub["destination"], sub["origin"]
    last = sub.get("last_delay_minutes")
    last_delay = int(last) if last is not None else None

    if status in ("landed", "arrived"):
        send_sms(sub["phone"], (
            f"RouteWise: {flight_id} has landed at {dest}. "
            f"Final delay: +{delay} min. Safe travels! ✈️"
        ))
        update_sub(sub["phone"], flight_id, {
            "status": "completed",
            "last_delay_minutes": delay,
        })
        return

    if last_delay == delay:
        return

    prev = last_delay or 0
    if prev == 0 and delay > 0:
        msg = (
            f"RouteWise: {flight_id} is now delayed. New departure: {new_time} "
            f"(+{delay} min). {origin}->{dest}."
        )
    elif delay == 0 and prev > 0:
        msg = f"RouteWise: {flight_id} delay cleared. Back on schedule - departs {new_time}."
    elif delay > prev:
        msg = (
            f"RouteWise: {flight_id} delay updated - now +{delay} min. "
            f"New departure: {new_time}."
        )
    else:
        msg = (
            f"RouteWise: {flight_id} delay reduced - now +{delay} min. "
            f"New departure: {new_time}."
        )

    send_sms(sub["phone"], msg)
    update_sub(sub["phone"], flight_id, {"last_delay_minutes": delay})


def should_run(days_until, hour, minute):
    if days_until > 14 or days_until < 0:
        return False
    if days_until == 0:
        return True
    if 3 <= days_until <= 14:
        return hour == 9 and minute < 15
    if 1 <= days_until <= 2:
        return hour in (8, 14, 20) and minute < 15
    return False


def process(sub, now):
    try:
        fd = date.fromisoformat(sub["flight_date"])
    except ValueError:
        return

    days_until = (fd - now.date()).days

    if days_until < 0:
        update_sub(sub["phone"], sub["flight_iata"], {"status": "completed"})
        return

    if not should_run(days_until, now.hour, now.minute):
        return

    if days_until == 0:
        handle_day_of(sub)
    else:
        handle_preflight(sub, days_until)


def scan_active():
    items, kwargs = [], {"FilterExpression": Attr("status").eq("active")}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def handler(_event, _context):
    now = datetime.now(timezone.utc)
    subs = scan_active()
    for sub in subs:
        try:
            process(sub, now)
        except Exception as e:
            print(f"error {sub.get('phone')}/{sub.get('flight_iata')}: {e}")
    return {"processed": len(subs)}
