import json
import os
import re
from datetime import datetime, timezone

import boto3

BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

AIRLINE_CODES = {
    "AA": "American Airlines", "UA": "United Airlines", "DL": "Delta Air Lines",
    "WN": "Southwest Airlines", "B6": "JetBlue Airways", "AS": "Alaska Airlines",
    "NK": "Spirit Airlines", "F9": "Frontier Airlines", "G4": "Allegiant Air",
    "SY": "Sun Country Airlines", "HA": "Hawaiian Airlines", "VX": "Virgin America",
    "MQ": "Envoy Air", "OO": "SkyWest Airlines", "YX": "Republic Airways",
    "9E": "Endeavor Air", "YV": "Mesa Airlines", "OH": "PSA Airlines",
}

def airline_from_iata(flight_iata: str) -> str:
    code = re.match(r"^([A-Z]{2})", flight_iata or "")
    if code:
        return AIRLINE_CODES.get(code.group(1), f"{code.group(1)} Airlines")
    return "Unknown"

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}

SYSTEM_PROMPT = (
    "You are a flight delay prediction system with knowledge of worldwide airline routes, "
    "schedules, and historical delay patterns. "
    "When inferring a flight's route or departure time from its flight number, only return "
    "values you are highly confident about from your training data. If you are uncertain "
    "about the exact route or departure time for a specific flight number, return null for "
    "those fields rather than guessing — a null is better than wrong data. "
    "Always return only a valid JSON object with no additional text or markdown."
)

USER_PROMPT = """Predict the delay probability for this flight:
- Flight: {flight_iata}
- Airline: {airline}
- Route: {origin} -> {destination} (if both are "???", infer from the flight number only if you are highly confident of the real-world route)
- Date: {day_of_week}, {month}
- Scheduled departure: {scheduled} (if "unknown", provide the real published departure time only if you are highly confident — otherwise return null)
- Distance: {distance} miles

Return JSON only - no markdown, no extra text:
{{
  "delay_probability": 0.0 to 1.0,
  "risk_level": "LOW" or "MEDIUM" or "HIGH",
  "explanation": "one sentence explaining the main delay risk factor, mentioning the route if known",
  "scheduled_departure_time": "HH:MM in local departure airport time if you are highly confident of the real published schedule, otherwise null",
  "inferred_origin": "IATA code of departure airport if it was ??? and you are highly confident, otherwise null",
  "inferred_destination": "IATA code of arrival airport if it was ??? and you are highly confident, otherwise null"
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


def call_bedrock(features):
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 600,
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

    if not flight_iata and not (origin and destination):
        return reply(400, {"error": "provide flight_iata or origin+destination"})

    try:
        dt = datetime.fromisoformat(flight_date)
    except ValueError:
        dt = datetime.now(timezone.utc)

    if departure_time:
        try:
            h, m = departure_time.split(":")
            dt = dt.replace(hour=int(h), minute=int(m))
        except Exception:
            pass

    feat = {
        "flight_iata": flight_iata or "",
        "airline": airline_from_iata(flight_iata) if flight_iata else "Unknown",
        "origin": origin or "???",
        "destination": destination or "???",
        "scheduled": f"{dt.hour:02d}:{dt.minute:02d}" if departure_time else "unknown",
        "day_of_week": dt.strftime("%A"),
        "month": dt.strftime("%B"),
        "distance": "unknown",
    }

    try:
        pred = call_bedrock(feat)
    except Exception as e:
        return reply(502, {"error": f"Bedrock error: {e}"})

    prob = float(pred.get("delay_probability", 0))
    resolved_origin = (feat["origin"] if feat["origin"] != "???" else None) or pred.get("inferred_origin") or "???"
    resolved_dest = (feat["destination"] if feat["destination"] != "???" else None) or pred.get("inferred_destination") or "???"
    return reply(200, {
        "flight_iata": flight_iata,
        "flight_date": flight_date,
        "airline": feat["airline"],
        "origin": resolved_origin,
        "destination": resolved_dest,
        "scheduled_departure": pred.get("scheduled_departure_time") or pred.get("typical_departure_time"),
        "current_status": None,
        "current_delay_minutes": None,
        "predicted_probability": prob,
        "risk_level": pred.get("risk_level") or risk_level(prob),
        "explanation": pred.get("explanation", ""),
    })
