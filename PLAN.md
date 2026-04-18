# RouteWise Implementation Plan

> Companion to [SPEC.md](SPEC.md). Read the spec alongside this plan.

## Current Status (as of UCI CloudHacks 2026)

**Everything below has already been completed.** The AWS infrastructure is live, all three Lambdas are deployed, and the mobile + web app are fully built. A developer picking this up only needs to handle the items marked **TODO**.

---

## What's Already Done

### AWS Infrastructure (live in us-east-1, account 701783520524)
- **DynamoDB** table `RouteWiseSubscriptions` — partition key `phone` (String), sort key `flight_iata` (String), on-demand billing
- **IAM role** `RouteWiseLambdaRole` — policies: `AWSLambdaBasicExecutionRole`, `AmazonDynamoDBFullAccess`, `AmazonSNSFullAccess`, `AmazonBedrockFullAccess`
- **Lambda A** `RouteWise-Predict` — deployed, handler `handler.handler`, Python 3.11, 512 MB, 30s timeout
- **Lambda B** `RouteWise-Subscribe` — deployed, same config
- **Lambda C** `RouteWise-Monitor` — deployed, same config
- **API Gateway** HTTP API `RouteWiseAPI` — invoke URL: `https://0wzwzcppz8.execute-api.us-east-1.amazonaws.com`
  - `POST /predict` → `RouteWise-Predict`
  - `POST /subscribe` → `RouteWise-Subscribe`
  - CORS: `*`
- **EventBridge** rule `RouteWise-Monitor-Schedule` — fires every 15 minutes → `RouteWise-Monitor`

### Mobile App (Expo React Native + Web)
- Full stack navigation: Search → Result screens
- Push notifications via Expo Push API (APNs token-based, key ID `ZD245WLS9Y`, team `7H5489C69N`)
- Web export works (`npx expo export --platform web` → `dist/`)
- EAS project linked: `477bd70b-0fef-49f6-b3c7-4b2c65183e90` (account: `goatedraider77`)
- Bundle ID: `com.cloudhacks.routewise`
- API URL hardcoded in `mobile/src/constants/config.ts`

### Key Architecture Decisions (locked in)
- **SMS replaced with push notifications** — no SNS SMS, uses Expo Push API (`https://exp.host/--/api/v2/push/send`)
- **Bedrock model:** `us.anthropic.claude-opus-4-6-v1` (inference profile, not raw model ID)
- **Aviationstack fallback:** Lambda A falls back to Bedrock-only prediction if Aviationstack is blocked (free tier blocks AWS IPs). Provide `origin`/`destination` in the request body for best results.
- **DynamoDB push_token storage:** `push_token` is stored in the `phone` partition key field — don't rename the DynamoDB column, just pass `push_token` as the value

---

## TODO for Next Developer

### 1. Deploy web app
The web build is at `mobile/dist/`. Deploy to any static host:

```bash
# Vercel (recommended)
cd mobile && npx vercel --prod dist/

# or Netlify
netlify deploy --prod --dir dist/
```

### 2. Upload APNs key to EAS (for production push notifications)
```bash
cd mobile
npx eas-cli credentials
# Select iOS → Push Notifications → Add new APNs Key
# Key path: /path/to/AuthKey_ZD245WLS9Y.p8
# Key ID: ZD245WLS9Y
# Team ID: 7H5489C69N
```

### 3. Build production iOS app (optional, for TestFlight)
```bash
cd mobile
eas build -p ios --profile production
eas submit -p ios
```

### 4. Redeploy Lambdas after any code changes
```bash
cd backend/lambdaA && zip -q function.zip handler.py
aws lambda update-function-code --function-name RouteWise-Predict --zip-file fileb://function.zip

cd ../lambdaB && zip -q function.zip handler.py
aws lambda update-function-code --function-name RouteWise-Subscribe --zip-file fileb://function.zip

cd ../lambdaC && zip -q function.zip handler.py
aws lambda update-function-code --function-name RouteWise-Monitor --zip-file fileb://function.zip
```

---

## Environment Variables (already set on each Lambda)

### RouteWise-Predict (Lambda A)
| Variable | Value |
|---|---|
| `AVIATIONSTACK_KEY` | `971c696ae0aa79579cc62ace149cf536` |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-opus-4-6-v1` |
| `BEDROCK_REGION` | `us-east-1` |

### RouteWise-Subscribe (Lambda B)
| Variable | Value |
|---|---|
| `DYNAMODB_TABLE` | `RouteWiseSubscriptions` |

### RouteWise-Monitor (Lambda C)
| Variable | Value |
|---|---|
| `AVIATIONSTACK_KEY` | `971c696ae0aa79579cc62ace149cf536` |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-opus-4-6-v1` |
| `DYNAMODB_TABLE` | `RouteWiseSubscriptions` |

> Note: `SNS_TOPIC_ARN` env var is no longer used — push notifications replaced SNS SMS.

---

## Running Locally

### Mobile (Expo Go on iPhone)
```bash
cd mobile
npm install
npx expo start
# Scan QR with Expo Go app
```

### Web
```bash
cd mobile
npx expo start --web
# Opens at http://localhost:8081
```

### Test backend with curl
```bash
API=https://0wzwzcppz8.execute-api.us-east-1.amazonaws.com

# Test predict
curl -X POST $API/predict \
  -H "Content-Type: application/json" \
  -d '{"flight_iata":"AA101","flight_date":"2026-04-20","origin":"JFK","destination":"LAX"}'

# Test subscribe (use a real Expo push token from the app)
curl -X POST $API/subscribe \
  -H "Content-Type: application/json" \
  -d '{"push_token":"ExponentPushToken[xxx]","flight_iata":"AA101","flight_date":"2026-04-20","origin":"JFK","destination":"LAX","scheduled_departure":"08:00","predicted_risk":0.45}'
```

---

## Risks / Watch-outs

- **Aviationstack free tier (100 req/mo):** Lambda A falls back to Bedrock-only if blocked. Include `origin`/`destination` in predict requests for best fallback accuracy.
- **Bedrock throttling:** `us.anthropic.claude-opus-4-6-v1` is the correct inference profile ID — not the raw model ID.
- **Expo push token:** only real physical devices running Expo Go (or a production build) produce valid push tokens. Simulators return null.
- **DynamoDB Scan in Lambda C:** fine for demo scale; add a GSI on `status` for production.
- **EventBridge every 15 min:** fires during demo — shows live monitoring. Consumes Aviationstack quota on day-of-flight subscriptions.
