import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import boto3

AVIATIONSTACK_KEY = os.environ["AVIATIONSTACK_KEY"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}

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


def reply(status, body):
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(body)}


def risk_level(p):
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


def aviationstack(params):
    qs = urllib.parse.urlencode({"access_key": AVIATIONSTACK_KEY, **params})
    url = f"http://api.aviationstack.com/v1/flights?{qs}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read()).get("data") or []


def pick_by_departure(flights, hhmm):
    if not hhmm:
        return flights[0] if flights else None
    for f in flights:
        dt = parse_iso((f.get("departure") or {}).get("scheduled"))
        if dt and dt.strftime("%H:%M") == hhmm:
            return f
    return flights[0] if flights else None


def features_from_flight(flight):
    dep = flight.get("departure") or {}
    arr = flight.get("arrival") or {}
    dt = parse_iso(dep.get("scheduled")) or datetime.now(timezone.utc)
    return {
        "airline": (flight.get("airline") or {}).get("name") or "Unknown",
        "origin": dep.get("iata") or "",
        "destination": arr.get("iata") or "",
        "dep_hour": dt.hour,
        "day_of_week": dt.strftime("%A"),
        "month": dt.strftime("%B"),
        "distance": "unknown",
    }


def call_bedrock(features):
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": USER_PROMPT.format(**features)}],
    }
    result = bedrock.invoke_model(modelId=BEDROCK_MODEL_ID, body=json.dumps(body))
    payload = json.loads(result["body"].read())
    text = JSON_FENCE.sub("", payload["content"][0]["text"]).strip()
    return json.loads(text)


def handler(event, _context):
    if (event.get("requestContext", {}).get("http", {}).get("method")
            or event.get("httpMethod")) == "OPTIONS":
        return reply(204, {})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return reply(400, {"error": "invalid JSON body"})

    flight_date = body.get("flight_date")
    if not flight_date:
        return reply(400, {"error": "flight_date is required"})

    flight_iata = body.get("flight_iata")
    origin = body.get("origin")
    destination = body.get("destination")
    departure_time = body.get("departure_time")

    try:
        if flight_iata:
            flights = aviationstack({"flight_iata": flight_iata, "flight_date": flight_date})
            flight = flights[0] if flights else None
        elif origin and destination:
            flights = aviationstack(
                {"dep_iata": origin, "arr_iata": destination, "flight_date": flight_date}
            )
            flight = pick_by_departure(flights, departure_time)
        else:
            return reply(400, {"error": "provide flight_iata or origin+destination+departure_time"})
    except Exception as e:
        return reply(502, {"error": f"aviationstack error: {e}"})

    if not flight:
        return reply(404, {"error": "no flight found"})

    feat = features_from_flight(flight)

    try:
        pred = call_bedrock(feat)
    except Exception as e:
        return reply(502, {"error": f"bedrock error: {e}"})

    prob = float(pred.get("delay_probability", 0))
    dep = flight.get("departure") or {}

    return reply(200, {
        "flight_iata": (flight.get("flight") or {}).get("iata") or flight_iata,
        "flight_date": flight_date,
        "airline": feat["airline"],
        "origin": feat["origin"],
        "destination": feat["destination"],
        "scheduled_departure": dep.get("scheduled"),
        "current_status": flight.get("flight_status"),
        "current_delay_minutes": dep.get("delay"),
        "predicted_probability": prob,
        "risk_level": pred.get("risk_level") or risk_level(prob),
        "explanation": pred.get("explanation", ""),
    })
