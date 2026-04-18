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


def risk_label(p):
    if p <= 0.40:
        return "LOW"
    if p <= 0.65:
        return "MEDIUM"
    return "HIGH"


def confirmation_message(flight, date, origin, dest, time, risk):
    pct = int(round(risk * 100))
    return (
        f"RouteWise: Watching {flight} on {date}. {origin}->{dest} {time}. "
        f"Predicted delay risk: {pct}% {risk_label(risk)}. "
        f"You'll be alerted if this flight is delayed."
    )


def handler(event, _context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "invalid JSON body"})

    required = [
        "phone",
        "flight_iata",
        "flight_date",
        "origin",
        "destination",
        "scheduled_departure",
        "predicted_risk",
    ]
    missing = [k for k in required if body.get(k) is None]
    if missing:
        return response(400, {"error": f"missing fields: {missing}"})

    phone = body["phone"]
    if not E164.match(phone):
        return response(400, {"error": "phone must be E.164, e.g. +13105551234"})

    predicted_risk = float(body["predicted_risk"])

    item = {
        "phone": phone,
        "flight_iata": body["flight_iata"],
        "flight_date": body["flight_date"],
        "origin": body["origin"],
        "destination": body["destination"],
        "scheduled_departure": body["scheduled_departure"],
        "predicted_risk": Decimal(str(predicted_risk)),
        "last_predicted_risk": Decimal(str(predicted_risk)),
        "last_delay_minutes": None,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    try:
        table.put_item(Item=item)
    except Exception as e:
        return response(500, {"error": f"dynamodb error: {e}"})

    message = confirmation_message(
        body["flight_iata"],
        body["flight_date"],
        body["origin"],
        body["destination"],
        body["scheduled_departure"],
        predicted_risk,
    )

    try:
        sns.publish(PhoneNumber=phone, Message=message)
    except Exception as e:
        return response(500, {"error": f"sns error: {e}"})

    return response(200, {"subscribed": True})
