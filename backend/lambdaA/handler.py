import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

import boto3

AVIATIONSTACK_KEY = os.environ["AVIATIONSTACK_KEY"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

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


def response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def aviationstack_get(params):
    qs = urllib.parse.urlencode({"access_key": AVIATIONSTACK_KEY, **params})
    url = f"http://api.aviationstack.com/v1/flights?{qs}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def pick_flight(data, departure_time=None):
    flights = data.get("data") or []
    if not flights:
        return None
    if departure_time:
        for f in flights:
            scheduled = (f.get("departure") or {}).get("scheduled") or ""
            if departure_time in scheduled:
                return f
    return flights[0]


def risk_level(p):
    if p <= 0.40:
        return "LOW"
    if p <= 0.65:
        return "MEDIUM"
    return "HIGH"


def call_bedrock(features):
    user_prompt = USER_PROMPT_TEMPLATE.format(**features)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    result = bedrock.invoke_model(modelId=BEDROCK_MODEL_ID, body=json.dumps(body))
    payload = json.loads(result["body"].read())
    text = payload["content"][0]["text"].strip()
    return json.loads(text)


def extract_features(flight):
    dep = flight.get("departure") or {}
    arr = flight.get("arrival") or {}
    airline = (flight.get("airline") or {}).get("name") or "Unknown"
    origin = dep.get("iata") or ""
    destination = arr.get("iata") or ""
    scheduled = dep.get("scheduled") or ""
    try:
        dt = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.utcnow()
    return {
        "airline": airline,
        "origin": origin,
        "destination": destination,
        "dep_hour": dt.hour,
        "day_of_week": dt.strftime("%A"),
        "month": dt.strftime("%B"),
        "distance": "unknown",
    }


def handler(event, _context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "invalid JSON body"})

    flight_iata = body.get("flight_iata")
    flight_date = body.get("flight_date")
    origin = body.get("origin")
    destination = body.get("destination")
    departure_time = body.get("departure_time")

    if not flight_date:
        return response(400, {"error": "flight_date is required"})

    try:
        if flight_iata:
            data = aviationstack_get({"flight_iata": flight_iata, "flight_date": flight_date})
            flight = pick_flight(data)
        elif origin and destination:
            data = aviationstack_get(
                {"dep_iata": origin, "arr_iata": destination, "flight_date": flight_date}
            )
            flight = pick_flight(data, departure_time=departure_time)
        else:
            return response(
                400,
                {"error": "provide flight_iata or (origin + destination + departure_time)"},
            )
    except Exception as e:
        return response(502, {"error": f"aviationstack error: {e}"})

    if not flight:
        return response(404, {"error": "no flight found"})

    features = extract_features(flight)

    try:
        prediction = call_bedrock(features)
    except Exception as e:
        return response(502, {"error": f"bedrock error: {e}"})

    prob = float(prediction.get("delay_probability", 0))
    dep = flight.get("departure") or {}

    return response(
        200,
        {
            "flight_iata": (flight.get("flight") or {}).get("iata") or flight_iata,
            "airline": features["airline"],
            "origin": features["origin"],
            "destination": features["destination"],
            "scheduled_departure": dep.get("scheduled"),
            "current_status": flight.get("flight_status"),
            "current_delay_minutes": dep.get("delay"),
            "predicted_probability": prob,
            "risk_level": prediction.get("risk_level") or risk_level(prob),
            "explanation": prediction.get("explanation", ""),
        },
    )
