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
    "You are a flight delay prediction system with deep knowledge of US domestic "
    "flight routes, schedules, and historical delay patterns. "
    "When a flight number is provided and origin/destination are unknown, use your "
    "training knowledge of common US airline routes to infer the typical route for "
    "that flight number. Always return only a valid JSON object with no additional "
    "text, preamble, or markdown."
)

USER_PROMPT = """Predict the delay probability for this flight:
- Flight: {flight_iata}
- Airline: {airline}
- Route: {origin} -> {destination} (if both are "???", infer the typical route from the flight number)
- Scheduled departure: {dep_hour:02d}:00 on {day_of_week}, {month}
- Distance: {distance} miles

Use your knowledge of this specific flight's typical route and schedule if live data is unavailable.

Return JSON only - no markdown, no extra text:
{{
  "delay_probability": 0.0 to 1.0,
  "risk_level": "LOW" or "MEDIUM" or "HIGH",
  "explanation": "one sentence explaining the main delay risk factor, mentioning the specific route if known"
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


def features_from_flight(flight, flight_iata=""):
    dep = flight.get("departure") or {}
    arr = flight.get("arrival") or {}
    dt = parse_iso(dep.get("scheduled")) or datetime.now(timezone.utc)
    return {
        "flight_iata": (flight.get("flight") or {}).get("iata") or flight_iata,
        "airline": (flight.get("airline") or {}).get("name") or "Unknown",
        "origin": dep.get("iata") or "???",
        "destination": arr.get("iata") or "???",
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

    # Accept pre-fetched features from mobile (bypasses Aviationstack IP block on free plan)
    prefetched = body.get("prefetched_features")
    if prefetched:
        print(f"[predict] using prefetched_features from mobile: airline={prefetched.get('airline')} "
              f"origin={prefetched.get('origin')} dest={prefetched.get('destination')} "
              f"dep_hour={prefetched.get('dep_hour')} status={prefetched.get('current_status')}")
        feat = {**prefetched, "flight_iata": flight_iata or prefetched.get("flight_iata", "")}
        try:
            pred = call_bedrock(feat)
        except Exception as e:
            return reply(502, {"error": f"Bedrock error: {e}"})
        prob = float(pred.get("delay_probability", 0))
        return reply(200, {
            "flight_iata": body.get("flight_iata"),
            "flight_date": flight_date,
            "airline": feat.get("airline", "Unknown"),
            "origin": feat.get("origin", "???"),
            "destination": feat.get("destination", "???"),
            "scheduled_departure": feat.get("scheduled_departure"),
            "current_status": feat.get("current_status"),
            "current_delay_minutes": feat.get("current_delay_minutes"),
            "predicted_probability": prob,
            "risk_level": pred.get("risk_level") or risk_level(prob),
            "explanation": pred.get("explanation", ""),
        })

    print("[predict] no prefetched_features — calling Aviationstack from Lambda (may be blocked on free plan)")
    flight = None
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
            return reply(400, {"error": "provide flight_iata or origin+destination"})
    except Exception as e:
        print(f"[predict] Aviationstack call failed: {e} — falling back to request fields only")

    # Build features from live data if available, otherwise use request fields
    if flight:
        feat = features_from_flight(flight, flight_iata)
    else:
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
            "airline": airline_from_iata(flight_iata),
            "origin": origin or "???",
            "destination": destination or "???",
            "dep_hour": dt.hour,
            "day_of_week": dt.strftime("%A"),
            "month": dt.strftime("%B"),
            "distance": "unknown",
        }

    try:
        pred = call_bedrock(feat)
    except Exception as e:
        return reply(502, {"error": f"Bedrock error: {e}"})

    prob = float(pred.get("delay_probability", 0))
    dep = (flight.get("departure") or {}) if flight else {}

    return reply(200, {
        "flight_iata": ((flight.get("flight") or {}).get("iata") if flight else None) or flight_iata,
        "flight_date": flight_date,
        "airline": feat["airline"],
        "origin": feat["origin"],
        "destination": feat["destination"],
        "scheduled_departure": dep.get("scheduled"),
        "current_status": flight.get("flight_status") if flight else None,
        "current_delay_minutes": dep.get("delay"),
        "predicted_probability": prob,
        "risk_level": pred.get("risk_level") or risk_level(prob),
        "explanation": pred.get("explanation", ""),
    })
