# HappyRobot workflow build guide

I don't have a HappyRobot platform account/sandbox myself, so I can't hand you
a live workflow link -- this doc is the exact, node-by-node spec to build it
in your account in roughly 30-45 minutes. Once built, grab the use case's
workflow URL from the platform and that's deliverable #4.

## 1. Use case + trigger

- Create a new use case: **"Inbound Carrier Sales"**.
- Trigger: **Web call** (not "Inbound to number" -- the brief explicitly
  says not to provision a phone number). This gives you a test widget /
  embeddable web-call trigger instead of a DID.
- Set `classification_tags`: `Booked`, `Failed - Authority`, `Failed - Identity`,
  `Failed - No Load`, `Failed - Negotiation`.
- Set `extract_with_ai` fields: `mc_number`, `carrier_name`, `equipment_type`,
  `lane_origin`, `lane_destination`, `load_id`, `loadboard_rate_offered`,
  `final_agreed_rate`, `negotiation_rounds`, `otp_verified`.

## 2. AI Agent -- Inbound Voice Agent node

Add immediately after the trigger. Configure:

- **Initial message**: "Thanks for calling HappyRobot Logistics carrier
  sales. Can I get your MC number to pull up available loads?"
- **Prompt**: use the system prompt from the architecture discussion above
  (paste it into the node's prompt field). Keyterms: add `MC number`,
  `load ID`, equipment type names, and any lane/city names common in your
  network so the transcriber catches them reliably.
- **Tools** attached to this node (each becomes a function call the agent
  can invoke mid-conversation):
  1. `verify_fmcsa` -- Webhook → `GET {FMCSA_API}/fmcsa/verify/{mc_number}`
  2. `generate_otp` -- Webhook → `POST {INTEGRATION_API}/otp/generate`
  3. `validate_otp` -- Webhook → `POST {INTEGRATION_API}/otp/validate`
  4. `search_loads` -- Webhook → `POST {INTEGRATION_API}/tms/search`
  5. `evaluate_negotiation` -- Webhook → `POST {INTEGRATION_API}/negotiation/evaluate`
  6. `book_load` -- Webhook → `POST {INTEGRATION_API}/tms/book`

  All six send `Authorization: Bearer <API_AUTH_TOKEN>` as a static header.

## 3. Branching logic

After `verify_fmcsa` returns, add a conditional branch:
- `active == false` → go to **Close call (no authority)** branch → set
  classification `Failed - Authority` → end call.
- `active == true` → continue.

After OTP flow (only triggered for carriers not already on file -- check
your CRM/Twin record for the MC number first; skip straight to load search
if already verified):
- `locked == true` (3 failed attempts) → **Close call (identity)** →
  classification `Failed - Identity` → end call.
- `valid == true` → continue to load search.

After `search_loads`:
- empty result → **Close call (no load)** → classification `Failed - No Load`
  → end call.
- match found → pitch the load (origin, destination, pickup/delivery window,
  equipment, `loadboard_rate` only -- the response from `/tms/search` never
  includes `max_rate`, so there's nothing to accidentally leak even if the
  prompt is jailbroken).

## 4. Negotiation loop

This is a loop construct around steps 4a-4c, max 3 iterations (track `round`
as a workflow variable, increment each pass):

1. **4a.** Capture the carrier's ask via Extract-with-AI.
2. **4b.** Call `evaluate_negotiation` with `{load_id, loadboard_rate,
   max_rate, carrier_ask, round}`. **Important**: `max_rate` is passed
   workflow-to-API only -- it's a variable populated from the `/tms/search`
   internal record (request it from `/tms/load/{id}` server-side context, or
   better, have the negotiation endpoint pull it itself by `load_id` so it
   never transits through the agent's variable space at all -- see note
   below).
3. **4c.** Branch on `decision`:
   - `accept` → go to step 5 (booking).
   - `counter` → agent says the `offer_amount`, round += 1, loop back to 4a.
   - `final_offer` → agent presents it as "the best rate I can offer," loop
     back to 4a one last time; if carrier's next ask is still above it →
     **Close call (negotiation failed)** → classification
     `Failed - Negotiation` → end call, **do not** trigger transfer.

> **Hardening note**: in this v1, the workflow needs `max_rate` as a
> parameter to call `/negotiation/evaluate`. Safer alternative for your
> production build: change the endpoint to `/negotiation/evaluate/{load_id}`
> and have it look up `max_rate` server-side via the TMS adapter, so the
> number never has to exist as a workflow variable the agent's context could
> theoretically reference. Noted as a fast follow in `docs/qa_and_kpis.md`.

## 5. Booking + handoff (mocked)

- Call `book_load` with `{load_id, mc_number, agreed_rate, call_id}`.
- Agent says: "Great, you're booked on load {load_id}. I'm transferring you
  to one of our senior reps to confirm the paperwork."
- Set `transfer_number` to a placeholder/mock number (per the brief, live
  transfer isn't required and doesn't work on web calls) -- or, simpler for
  the demo, skip the literal transfer node and just end the call after the
  message, with classification `Booked`. Document in your walkthrough that
  this is the mocked handoff point.

## 6. Logging

No extra node needed for the audit trail -- `extract_with_ai` and
`classification_tags` are captured automatically into Twin for every run.
The Apps dashboard (next section) reads from there.

## 7. Apps dashboard (operational UI)

Build a HappyRobot Apps view for the ops manager with:
- Calls today, by outcome (the 5 classification tags) -- bar or count cards.
- Average negotiation rounds to close.
- Booked loads list with carrier, load_id, agreed_rate, timestamp.
- Failed-authority and failed-identity counts (compliance signal).

No external UI needed for this POC -- all the fields it needs are already in
the extract/classification schema above.
