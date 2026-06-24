# HappyRobot Logistics -- carrier sales integration API

Backend integration service for the inbound carrier sales automation POC.
Bridges the HappyRobot voice agent workflow (via Webhook action nodes) to:

- the legacy TMS (TCP, fixed-width protocol) -- load search, detail, booking
- OTP generation, delivery hand-off, and validation
- a deterministic rate-negotiation engine that enforces the `max_rate`
  ceiling in code, not just in the agent's prompt
- FMCSA carrier authority verification (optional wrapper -- FMCSA is already
  REST, so the workflow can call it directly; this exists mainly so the repo
  is runnable without a live FMCSA web key)

See `docs/protocol_assumptions.md` for the TMS wire-format assumptions, and
`docs/workflow_spec.md` for the exact HappyRobot platform workflow this API
is meant to be called from.

## Quick start

```bash
cp .env.example .env        # edit API_AUTH_TOKEN at minimum
docker compose up --build
```

This brings up two containers:
- `integration-api` on `:8000` -- the service above
- `mock-tms` on `:9999` -- a self-contained stand-in for the customer's real
  TMS, so the whole thing is demoable without their credentials. Swap
  `TMS_HOST`/`TMS_PORT` in `.env` to point at the real TMS and drop the
  `mock-tms` service for production.

Check it's up:

```bash
curl http://localhost:8000/health
```

Try a load search (replace `change-me` with your `API_AUTH_TOKEN`):

```bash
curl -X POST http://localhost:8000/tms/search \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{"origin": "Chicago, IL", "equipment_type": "dry_van"}'
```

## Local development (without Docker)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# terminal 1: mock TMS
python -m app.mocks.mock_tms_server

# terminal 2: API
uvicorn app.main:app --reload

# terminal 3: tests
pytest tests/ -v
```

## Endpoints

All endpoints except `/health` require `Authorization: Bearer <API_AUTH_TOKEN>`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/tms/search` | Search loads by origin/destination/equipment. Never returns `max_rate`. |
| GET | `/tms/load/{load_id}` | Load detail. Never returns `max_rate`. |
| POST | `/tms/book` | Tentatively reserve a load at the agreed rate. |
| POST | `/otp/generate` | Generate + "send" (stubbed) an OTP for a call. |
| POST | `/otp/resend` | One resend allowed; second resend burns an attempt. |
| POST | `/otp/validate` | Validate a code; locks after 3 wrong attempts. |
| POST | `/negotiation/evaluate` | Deterministic next move given round + carrier ask. |
| GET | `/fmcsa/verify/{mc_number}` | Carrier authority check (mock mode by default). |

## Test suite

```bash
pytest tests/ -v
```

47 tests across standard, edge-case, and adversarial scenarios. See
`docs/qa_and_kpis.md` for the full QA report and the northstar KPIs this
build is meant to move.

The single most important test in the repo is
`tests/test_api_integration.py::test_search_response_never_contains_max_rate_key_anywhere_in_payload`
-- it asserts the ceiling never leaves the server, at the HTTP boundary,
regardless of how the negotiation logic evolves later.

## Project layout

```
app/
  main.py                 FastAPI app
  config.py                env-driven settings
  auth.py                   bearer-token dependency
  models.py                  pydantic schemas (shared contract)
  routers/                    tms.py, otp.py, negotiation.py, fmcsa.py
  services/
    tms_client.py              TCP client: retries, backoff, circuit breaker
    otp_store.py                 TTL + attempt-limited OTP store
    negotiation_engine.py          deterministic rate logic
    fmcsa_client.py                FMCSA REST client (+ mock mode)
  mocks/
    mock_tms_server.py             self-contained TMS double for demo/tests
tests/                              standard + edge + adversarial, 47 tests
docs/
  protocol_assumptions.md            documented TMS wire-format assumption
  workflow_spec.md                    node-by-node HappyRobot build guide
  qa_and_kpis.md                       KPIs + QA report
```
