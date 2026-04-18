import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal

import boto3

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")
table = dynamodb.Table(DYNAMODB_TABLE)

E164 = re.compile(r"^\+[1-9]\d{1,14}$")

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}

REQUIRED = (
    "phone",
    "flight_iata",
    "flight_date",
    "origin",
    "destination",
    "scheduled_departure",
    "predicted_risk",
)


def reply(status, body):
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(body)}


def risk_label(p):
    if p <= 0.40:
        return "LOW"
    if p <= 0.65:
        return "MEDIUM"
    return "HIGH"


def confirmation_sms(sub):
    pct = round(float(sub["predicted_risk"]) * 100)
    return (
        f"RouteWise: Watching {sub['flight_iata']} on {sub['flight_date']}. "
        f"{sub['origin']}->{sub['destination']} {sub['scheduled_departure']}. "
        f"Predicted delay risk: {pct}% {risk_label(float(sub['predicted_risk']))}. "
        f"You'll be alerted if this flight is delayed."
    )


def handler(event, _context):
    if (event.get("requestContext", {}).get("http", {}).get("method")
            or event.get("httpMethod")) == "OPTIONS":
        return reply(204, {})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return reply(400, {"error": "invalid JSON body"})

    missing = [k for k in REQUIRED if body.get(k) is None]
    if missing:
        return reply(400, {"error": f"missing fields: {missing}"})

    if not E164.match(body["phone"]):
        return reply(400, {"error": "phone must be E.164 (e.g. +13105551234)"})

    risk = float(body["predicted_risk"])
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    item = {
        "phone": body["phone"],
        "flight_iata": body["flight_iata"],
        "flight_date": body["flight_date"],
        "origin": body["origin"],
        "destination": body["destination"],
        "scheduled_departure": body["scheduled_departure"],
        "predicted_risk": Decimal(str(risk)),
        "last_predicted_risk": Decimal(str(risk)),
        "last_delay_minutes": None,
        "status": "active",
        "created_at": now,
    }

    try:
        table.put_item(Item=item)
    except Exception as e:
        return reply(500, {"error": f"dynamodb error: {e}"})

    try:
        sns.publish(PhoneNumber=body["phone"], Message=confirmation_sms(body))
    except Exception as e:
        return reply(500, {"error": f"sns error: {e}"})

    return reply(200, {"subscribed": True})
