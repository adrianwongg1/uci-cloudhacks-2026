# RouteWise — Full Specification

## What It Does
A flight delay predictor app. User enters a flight number or route + date.
App predicts delay probability using Amazon Bedrock, then watches the flight
in real time and sends SMS alerts every time the delay status changes until
the plane lands.

## Frontend
- Expo React Native app (demo on iPhone via Expo Go tomorrow)
- TestFlight distribution after hackathon via EAS build/submit
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
- Phone number input + Subscribe button
- Post-subscription confirmation state

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
Input: { phone, flight_iata, flight_date, origin, destination, scheduled_departure, predicted_risk }
Steps:
1. Save subscription to DynamoDB
2. Subscribe phone number to SNS topic
3. Send confirmation SMS immediately

Confirmation SMS:
"RouteWise: Watching [flight] on [date]. [origin]→[dest] [time].
Predicted delay risk: [X]% [LEVEL]. You'll be alerted if this flight is delayed."

### Lambda C — Polling (triggered by EventBridge every 15 min)
Logic per subscription based on days_until_flight:
- > 14 days: no alerts
- 7-14 days: once/day at 9am — Bedrock re-prediction, SMS if risk changed >5%
- 3-6 days: once/day at 9am — same
- 1-2 days: 3x/day at 8am, 2pm, 8pm — same
- Day of (0 days): every 15 min — Aviationstack live status,
  SMS if delay_minutes changed, auto-unsubscribe on landing
- Past flight: mark status=completed, stop processing

SMS Templates:
- Subscription confirmed: "RouteWise: Watching [flight] on [date]. [origin]→[dest] [time]. Predicted delay risk: [X]% [LEVEL]. You'll be alerted if this flight is delayed."
- Delay starts: "RouteWise: [flight] is now delayed. New departure: [time] (+[X] min). [origin]→[dest]."
- Delay increases: "RouteWise: [flight] delay updated — now +[X] min. New departure: [time]."
- Delay decreases: "RouteWise: [flight] delay reduced — now +[X] min. New departure: [time]."
- Delay cleared: "RouteWise: [flight] delay cleared. Back on schedule — departs [time]."
- Flight landed: "RouteWise: [flight] has landed at [dest]. Final delay: +[X] min. Safe travels! ✈️"
- Pre-flight update (7-14 days): "RouteWise: [X] days until [flight] ([date]). [origin]→[dest] [time]. Current risk: [X]% [LEVEL]. We'll keep watching."
- Pre-flight update (1-2 days): "RouteWise: [X] days until [flight]. [origin]→[dest] [time]. Delay risk updated: [X]% [LEVEL]."

## DynamoDB Schema
Table name: RouteWiseSubscriptions
Partition key: phone (String)
Sort key: flight_iata (String)

Fields:
{
  "phone": "+13105551234",
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
