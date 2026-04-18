# RouteWise — Full Specification

## What It Does
A flight delay predictor app. User enters a flight number or route + date.
App predicts delay probability using Amazon Bedrock, then watches the flight
in real time and sends **push notifications** every time the delay status changes until
the plane lands.

> **Note:** SMS (SNS) was replaced with Expo Push Notifications. See Lambda B/C for details.

## Frontend
- Expo React Native app — runs on iOS (Expo Go) and web (`npx expo start --web`)
- EAS project: `477bd70b-0fef-49f6-b3c7-4b2c65183e90` (account: `goatedraider77`)
- Bundle ID: `com.cloudhacks.routewise`
- APNs key: `AuthKey_ZD245WLS9Y.p8`, Key ID `ZD245WLS9Y`, Team ID `7H5489C69N`
- Web build output: `mobile/dist/` (deploy to Vercel/Netlify)
- 2 screens:

### Screen 1 — Search
Two search modes (segmented control):
1. Flight Number mode: flight number input + date picker
2. Route mode: origin airport + destination airport + date + departure time
Single "Check Delay Risk" button

### Screen 2 — Result + Subscribe
Displays:
- Flight details (flight number, airline, origin, destination, scheduled departure)
- Delay probability % with risk level badge (LOW/MEDIUM/HIGH, color coded green/yellow/red)
- Bedrock explanation card (why this flight is high/low risk)
- Current live status from Aviationstack
- Subscribe button (uses Expo Push Token — no phone input needed)
- Post-subscription confirmation state
- On web: shows "Download the app" prompt instead of subscribe button

## Backend — AWS Services

### Lambda A — /predict (POST)
Input: { flight_iata, flight_date } OR { origin, destination, flight_date, departure_time }
Steps:
1. If route provided: call Aviationstack to find flight at exact scheduled departure time
2. If flight number provided: call Aviationstack to get flight details
3. Extract features: origin, destination, airline, dep_hour, day_of_week, month, distance
4. Call Amazon Bedrock with flight features → returns delay_probability (0-1) + explanation
5. Return full result to frontend

Output:
{
  flight_iata, airline, origin, destination,
  scheduled_departure, current_status,
  current_delay_minutes, predicted_probability,
  risk_level, explanation
}

### Lambda B — /subscribe (POST)
Input: { push_token, flight_iata, flight_date, origin, destination, scheduled_departure, predicted_risk }

> ⚠️ Field is named `push_token` in the request body, but stored in the DynamoDB `phone` partition key field. Do NOT rename the DynamoDB column.

Steps:
1. Save subscription to DynamoDB (push_token stored as `phone` key)
2. Send confirmation push notification via Expo Push API

Expo Push API endpoint: `https://exp.host/--/api/v2/push/send`
Payload: `{ "to": push_token, "title": "...", "body": "...", "sound": "default" }`

No SNS required. No phone number required.

### Lambda C — Polling (triggered by EventBridge every 15 min)
Logic per subscription based on days_until_flight:
- > 14 days: no alerts
- 7-14 days: once/day at 9am — Bedrock re-prediction, push if risk changed >5%
- 3-6 days: once/day at 9am — same
- 1-2 days: 3x/day at 8am, 2pm, 8pm — same
- Day of (0 days): every 15 min — Aviationstack live status,
  push if delay_minutes changed, auto-unsubscribe on landing
- Past flight: mark status=completed, stop processing

Push Notification Templates (title / body):
- Confirmed: title="Watching [flight] ✈️" body="[origin]→[dest] on [date] at [time]. Risk: [X]% ([LEVEL])."
- Delay starts: title="[flight] is delayed" body="New departure: [time] (+[X] min). [origin]→[dest]."
- Delay increases: title="[flight] delay increased" body="Now +[X] min. New departure: [time]."
- Delay decreases: title="[flight] delay reduced" body="Now +[X] min. New departure: [time]."
- Delay cleared: title="[flight] delay cleared ✅" body="Back on schedule — departs [time]."
- Landed: title="[flight] has landed ✈️" body="Arrived at [dest]. Final delay: +[X] min. Safe travels!"
- Pre-flight update: title="[flight] delay risk update" body="[origin]→[dest] on [date]. Risk now [X]% [LEVEL]."

## DynamoDB Schema
Table name: RouteWiseSubscriptions
Partition key: `phone` (String) — **stores the Expo push token, not a phone number**
Sort key: `flight_iata` (String)

Fields:
{
  "phone": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
  "flight_iata": "AA101",
  "flight_date": "2026-04-20",
  "origin": "LAX",
  "destination": "JFK",
  "scheduled_departure": "09:15",
  "predicted_risk": 0.71,
  "last_predicted_risk": 0.71,
  "last_delay_minutes": null,
  "status": "active",
  "created_at": "2026-04-18T08:00:00Z"
}

## External APIs
- Aviationstack: real-time flight status and route lookup
  - GET /flights?flight_iata=AA101&flight_date=2026-04-20
  - GET /flights?dep_iata=LAX&arr_iata=JFK&flight_date=2026-04-20
  - API key stored in Lambda environment variable: AVIATIONSTACK_KEY

## AWS Stack
- Lambda A, B, C (Python 3.11)
- API Gateway (POST /predict, POST /subscribe)
- DynamoDB (RouteWiseSubscriptions table)
- SNS (RouteWiseTopic)
- EventBridge (cron every 15 min → Lambda C)
- Bedrock (claude-sonnet for prediction + explanation)

## Bedrock Prompt Structure
System prompt:
"You are a flight delay prediction system. You have deep knowledge of US
domestic flight delay patterns based on historical data. Always return
only a valid JSON object with no additional text, preamble, or markdown."

User prompt:
"Predict the delay probability for this flight:
- Route: [origin] → [destination]
- Airline: [airline]
- Scheduled departure: [dep_hour]:00 on [day_of_week], [month]
- Distance: [distance] miles

Return JSON only — no markdown, no extra text:
{
  \"delay_probability\": 0.0 to 1.0,
  \"risk_level\": \"LOW\" or \"MEDIUM\" or \"HIGH\",
  \"explanation\": \"one sentence explaining the main delay risk factor\"
}"

Risk level thresholds:
- LOW: 0.0 - 0.40
- MEDIUM: 0.41 - 0.65
- HIGH: 0.66 - 1.0

## Polling Schedule (Lambda C)
EventBridge rule: cron(0/15 * * * ? *) — fires every 15 min always
Lambda C internally decides what to do based on days_until_flight:

days_until_flight > 14:    do nothing
days_until_flight 7-14:    run at hour == 9 only
days_until_flight 3-6:     run at hour == 9 only
days_until_flight 1-2:     run at hour in [8, 14, 20]
days_until_flight == 0:    run every invocation (every 15 min)
days_until_flight < 0:     mark completed, skip

Pre-flight check logic (days 1-14):
- Call Bedrock for updated prediction
- If abs(new_probability - last_predicted_risk) > 0.05: send SMS + update DynamoDB
- If no significant change: do nothing (silence = good news)

Day-of check logic (day 0):
- Call Aviationstack for live status
- If current_delay_minutes != last_delay_minutes: send SMS + update DynamoDB
- If status == "landed" or "arrived": send final SMS + set status = "completed"

## File Structure
routewise/
├── mobile/
│   ├── App.tsx
│   ├── app.json
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── screens/
│       │   ├── SearchScreen.tsx
│       │   └── ResultScreen.tsx
│       ├── api/
│       │   ├── predict.ts
│       │   └── subscribe.ts
│       ├── components/
│       │   ├── RiskBadge.tsx
│       │   ├── FlightCard.tsx
│       │   └── ExplanationCard.tsx
│       ├── types/
│       │   └── index.ts
│       └── constants/
│           └── config.ts        ← API Gateway base URL stored here
├── backend/
│   ├── lambdaA/
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── lambdaB/
│   │   ├── handler.py
│   │   └── requirements.txt
│   └── lambdaC/
│       ├── handler.py
│       └── requirements.txt
└── SPEC.md

## Environment Variables
Lambda A:
  AVIATIONSTACK_KEY=your_key_here
  BEDROCK_MODEL_ID=claude-sonnet-4-20250514
  BEDROCK_REGION=us-east-1

Lambda B:
  SNS_TOPIC_ARN=arn:aws:sns:us-east-1:...
  DYNAMODB_TABLE=RouteWiseSubscriptions

Lambda C:
  AVIATIONSTACK_KEY=your_key_here
  BEDROCK_MODEL_ID=claude-sonnet-4-20250514
  SNS_TOPIC_ARN=arn:aws:sns:us-east-1:...
  DYNAMODB_TABLE=RouteWiseSubscriptions

## API Gateway Endpoints
POST /predict
  Request: { "flight_iata": "AA101", "flight_date": "2026-04-20" }
       OR: { "origin": "LAX", "destination": "JFK",
              "flight_date": "2026-04-20", "departure_time": "09:15" }
  Response: { flight_iata, airline, origin, destination,
              scheduled_departure, current_status,
              current_delay_minutes, predicted_probability,
              risk_level, explanation }

POST /subscribe
  Request: { "phone": "+13105551234", "flight_iata": "AA101",
             "flight_date": "2026-04-20", "origin": "LAX",
             "destination": "JFK", "scheduled_departure": "09:15",
             "predicted_risk": 0.71 }
  Response: { "subscribed": true }

## Demo Script (Hackathon Day)
1. Open app on iPhone — show Screen 1
2. Enter AA101, date 04/20/2026 → tap Check Delay Risk
3. Screen 2 loads — show probability %, risk badge, Bedrock explanation
4. Enter real phone number → tap Subscribe
5. Phone buzzes — show confirmation SMS live
6. Open AWS console — show DynamoDB entry saved
7. Show EventBridge rule — explain 15 min polling
8. Show Lambda C code — explain delay change detection
9. Closing line: "RouteWise predicts your delay risk before you fly
   and keeps you informed in real time until you land."

## Setup Instructions (Night Before Hackathon)
1. Sign up for Aviationstack free tier at aviationstack.com — get API key
2. Confirm AWS hackathon account credentials
3. Install tools:
   npm install -g expo-cli eas-cli
4. Create Expo project:
   npx create-expo-app routewise --template blank-typescript
5. Install dependencies:
   npx expo install @react-navigation/native @react-navigation/stack
   npx expo install react-native-screens react-native-safe-area-context
6. Create Expo account at expo.dev

## Post-Hackathon — TestFlight Upload
1. eas build --platform ios
2. eas submit --platform ios
3. Add testers in App Store Connect → TestFlight tab
4. Testers receive email → install TestFlight → install RouteWise

## Claude Code Kickoff Prompt
Open terminal in project folder and run: claude
Then say:
"Read SPEC.md and create a plan for building RouteWise before writing any code."
