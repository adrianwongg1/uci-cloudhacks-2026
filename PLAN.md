# RouteWise Implementation Plan

> Companion to [SPEC.md](SPEC.md). Read the spec alongside this plan — this plan only covers build order and the decisions the spec left open.

## Context

RouteWise is a hackathon flight-delay predictor for UCI CloudHacks 2026. The spec is detailed and self-contained: file layout, API contracts, DynamoDB schema, SMS templates, Bedrock prompt, polling logic, and env vars are all pinned down. The repo is already initialized and the full folder tree from the spec is scaffolded with empty files, committed, and pushed to `github.com/adrianwongg1/uci-cloudhacks-2026`.

Goal: give a single developer (on a different laptop later in the day) an unambiguous execution order to fill in the empty files, stand up the AWS stack the fastest way possible, and reach a demoable state on iPhone via Expo Go.

### Decisions locked in

- **AWS deploy style:** fastest/easiest — **manual AWS Console clicks + `aws` CLI zip uploads** for Lambdas. No SAM, no CDK.
- **Bedrock model:** **Claude Opus 4.7** (`claude-opus-4-7`). Supersedes `claude-sonnet-4-20250514` in the spec. Use the exact Bedrock inference-profile ID returned by `aws bedrock list-inference-profiles --region us-east-1` at deploy time (likely `us.anthropic.claude-opus-4-7-YYYYMMDD-v1:0`). Update `BEDROCK_MODEL_ID` env var on Lambdas A and C.

## Prerequisites (do these first on the new laptop)

1. `git clone https://github.com/adrianwongg1/uci-cloudhacks-2026.git routewise && cd routewise`
2. Node 20+, Python 3.11, AWS CLI v2, `npm i -g expo-cli eas-cli`.
3. `aws configure` with hackathon credentials in `us-east-1`.
4. Aviationstack API key in hand (free tier at aviationstack.com).
5. Expo account logged in (`eas login`).
6. In Bedrock console (`us-east-1`): request & confirm access to **Claude Opus 4.7**. Copy the inference profile ID — this is the value of `BEDROCK_MODEL_ID`.

## Phase 1 — AWS infrastructure (manual console, ~30 min)

Create resources in this order so Lambda env vars have their ARNs ready.

1. **DynamoDB** → table `RouteWiseSubscriptions`, partition key `phone` (String), sort key `flight_iata` (String), on-demand billing.
2. **SNS** → topic `RouteWiseTopic`. Copy ARN → `SNS_TOPIC_ARN`. Enable SMS (default region `us-east-1`; set SMS type to "Transactional" in SNS text-messaging preferences; request spending limit increase if sandbox-limited).
3. **IAM role** `RouteWiseLambdaRole` with managed policies: `AWSLambdaBasicExecutionRole`, `AmazonDynamoDBFullAccess`, `AmazonSNSFullAccess`, `AmazonBedrockFullAccess`. (Hackathon-grade scoping — tighten later.)
4. **Lambdas** (Python 3.11, role = `RouteWiseLambdaRole`, timeout 30 s, memory 512 MB):
   - `RouteWise-Predict` (Lambda A)
   - `RouteWise-Subscribe` (Lambda B)
   - `RouteWise-Poll` (Lambda C)

   Set env vars per spec §"Environment Variables" (with updated `BEDROCK_MODEL_ID`).
5. **API Gateway** HTTP API `RouteWiseAPI`:
   - `POST /predict` → `RouteWise-Predict`
   - `POST /subscribe` → `RouteWise-Subscribe`
   - Enable CORS `*`. Copy invoke URL → mobile `src/constants/config.ts`.
6. **EventBridge** rule `RouteWisePoll` → `cron(0/15 * * * ? *)` → target `RouteWise-Poll`.

## Phase 2 — Backend Lambdas (fill in empty handlers)

All three live under [backend/](backend/) with empty `handler.py` + `requirements.txt`.

### [backend/lambdaA/handler.py](backend/lambdaA/handler.py) — `/predict`
- Parse `event["body"]` JSON. Branch on presence of `flight_iata` vs `origin`+`destination`+`departure_time`.
- Call Aviationstack via `urllib.request` (stdlib; avoid `requests` to keep zip small).
- Derive features: `dep_hour` from scheduled_departure, `day_of_week`, `month`, `distance`. Simplest: let Bedrock estimate distance from the airport codes in the prompt — no airport table needed.
- Call Bedrock via `boto3.client("bedrock-runtime").invoke_model(...)` with `anthropic_version: "bedrock-2023-05-31"`. System + user prompt verbatim from spec §"Bedrock Prompt Structure".
- Parse JSON response, compute `risk_level` from thresholds (LOW ≤0.40, MEDIUM 0.41–0.65, HIGH ≥0.66).
- Return the exact output shape from spec §"Lambda A".
- `requirements.txt`: empty (boto3 is in the Lambda runtime).

### [backend/lambdaB/handler.py](backend/lambdaB/handler.py) — `/subscribe`
- Validate phone is E.164.
- `boto3.resource("dynamodb").Table(...).put_item(...)` with all fields from spec §"DynamoDB Schema". Set `last_predicted_risk = predicted_risk`, `last_delay_minutes = None`, `status = "active"`, `created_at = datetime.utcnow().isoformat()+"Z"`.
- **Recommend `sns.publish(PhoneNumber=phone, Message=...)` direct-publish** instead of topic subscription — SMS topic subscription is fiddly in sandbox mode.
- Send the "Subscription confirmed" SMS template from spec §"SMS Templates".
- Return `{"subscribed": true}`.
- `requirements.txt`: empty.

### [backend/lambdaC/handler.py](backend/lambdaC/handler.py) — Polling
- Scan `RouteWiseSubscriptions` where `status = "active"` (filter expression).
- For each subscription, compute `days_until_flight` and current UTC hour.
- Gate with the exact decision table in spec §"Polling Schedule (Lambda C)".
- **Days 1–14:** reuse Bedrock call from Lambda A. If `abs(new - last_predicted_risk) > 0.05` → send pre-flight SMS, update `last_predicted_risk`.
- **Day 0:** call Aviationstack live status. If `delay_minutes` changed → send matching SMS (starts / increases / decreases / cleared). On `landed`/`arrived` → send landed SMS and set `status = "completed"`.
- Past-date: set `status = "completed"`, skip.
- `requirements.txt`: empty.

### Deploy each Lambda (CLI)
```bash
cd backend/lambdaA && zip -r function.zip . && \
  aws lambda update-function-code --function-name RouteWise-Predict --zip-file fileb://function.zip
```
Repeat for B (`RouteWise-Subscribe`) and C (`RouteWise-Poll`). If dependencies get added later, `pip install -t .` into the folder before zipping.

## Phase 3 — Mobile app (Expo React Native)

The spec lists the exact files under [mobile/src/](mobile/src/). They're empty — fill them:

1. **Do not run `create-expo-app`** — it would conflict with the committed layout. Instead, paste a minimal `package.json` with `expo`, `react`, `react-native`, `@react-navigation/native`, `@react-navigation/stack`, `react-native-screens`, `react-native-safe-area-context`, `@react-native-community/datetimepicker`, `expo-status-bar`. Matching `app.json` and `tsconfig.json` (extends `expo/tsconfig.base`).
2. [mobile/src/constants/config.ts](mobile/src/constants/config.ts) — export `API_BASE_URL` = API Gateway invoke URL from Phase 1.
3. [mobile/src/types/index.ts](mobile/src/types/index.ts) — TS interfaces mirroring Lambda A output and Lambda B input shapes.
4. [mobile/src/api/predict.ts](mobile/src/api/predict.ts) / [subscribe.ts](mobile/src/api/subscribe.ts) — `fetch` wrappers returning typed responses.
5. Components:
   - [RiskBadge.tsx](mobile/src/components/RiskBadge.tsx) — colored pill keyed off `risk_level` (green/yellow/red).
   - [FlightCard.tsx](mobile/src/components/FlightCard.tsx) — flight number, airline, route, scheduled dep.
   - [ExplanationCard.tsx](mobile/src/components/ExplanationCard.tsx) — Bedrock `explanation` string.
6. Screens:
   - [SearchScreen.tsx](mobile/src/screens/SearchScreen.tsx) — segmented control (Flight # vs Route), form fields, "Check Delay Risk" button → `predict` → navigate to Result.
   - [ResultScreen.tsx](mobile/src/screens/ResultScreen.tsx) — FlightCard + RiskBadge + ExplanationCard + live status + phone input + Subscribe button + post-subscribe confirmation state.
7. [App.tsx](mobile/App.tsx) — `NavigationContainer` + stack navigator (Search → Result).

## Phase 4 — End-to-end verification

1. **Backend sanity (before touching the phone):**
   ```bash
   curl -X POST <api>/predict -H "Content-Type: application/json" \
     -d '{"flight_iata":"AA101","flight_date":"2026-04-20"}'
   curl -X POST <api>/subscribe -H "Content-Type: application/json" \
     -d '{"phone":"+1...","flight_iata":"AA101", ...}'
   ```
   Expect full response shape, SMS within ~10 s, and a row in DynamoDB. Check CloudWatch on failure.
2. **Polling Lambda:** Lambda console → "Test" with `{}` event. Verify branch logic in CloudWatch. Force a row with `flight_date = today` to exercise the Aviationstack path.
3. **Mobile:** `cd mobile && npx expo start`, scan QR with Expo Go on iPhone. Run the full Demo Script from spec §"Demo Script (Hackathon Day)".
4. **Edge cases:** unknown flight number, route with no flight at that departure time, Aviationstack rate limit (free tier = 100/mo), Bedrock throttling.

## Phase 5 — Demo-day polish (only if time)

- Loading + error states on both screens.
- Disable Subscribe button until phone passes E.164 regex.
- "Test with AA101" demo shortcut button on SearchScreen.
- TestFlight (`eas build -p ios` → `eas submit -p ios`) — kick off during demo; builds take ~20 min.

## Critical files to modify

- `backend/lambdaA/handler.py`, `backend/lambdaB/handler.py`, `backend/lambdaC/handler.py` (+ `requirements.txt`, likely stays empty)
- `mobile/package.json`, `mobile/app.json`, `mobile/tsconfig.json`, `mobile/App.tsx`
- `mobile/src/constants/config.ts`, `mobile/src/types/index.ts`
- `mobile/src/api/predict.ts`, `mobile/src/api/subscribe.ts`
- `mobile/src/components/{RiskBadge,FlightCard,ExplanationCard}.tsx`
- `mobile/src/screens/{Search,Result}Screen.tsx`

No files outside the committed scaffold need to be created.

## Risks / watch-outs

- **SNS SMS sandbox:** new AWS accounts can only SMS verified numbers. Verify your demo phone in the SNS console before the demo.
- **Aviationstack free tier:** 100 req/mo. Budget calls; stub responses if you burn the quota.
- **Bedrock access:** Opus 4.7 access-request approval can take minutes — do this first on the new laptop.
- **DynamoDB Scan in Lambda C:** fine for demo scale; would need a GSI at prod volume.
- **EventBridge every 15 min:** it will fire during the demo (good — shows it's live) but consumes Aviationstack quota.
