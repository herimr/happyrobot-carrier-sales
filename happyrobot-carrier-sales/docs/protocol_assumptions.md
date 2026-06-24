# Legacy TMS protocol -- assumptions

The challenge brief references a "fixed-width line protocol" for the legacy
TMS but does not include the actual protocol spec document (the PDF links to
one but its contents weren't provided to me). To build something testable
end-to-end, this repo implements a **documented, reasonable assumption** of
that protocol, used consistently by both the adapter client
(`app/services/tms_client.py`) and the mock server
(`app/mocks/mock_tms_server.py`) used for local development and tests.

**This must be validated against the real protocol spec before connecting to
the customer's actual TMS.** Swapping in the real format only requires
changing the encode/decode functions in `tms_client.py` -- the retry/circuit
breaker/REST-bridge logic around it does not change.

## Assumed wire format

All requests and responses are ASCII, newline-terminated, fixed-width fields
(space-padded, left-aligned for text, zero-padded right-aligned for numbers).

### Request header (all commands)

| Bytes | Field | Width |
|---|---|---|
| 0-3 | command code (`SRCH`, `DETL`, `BOOK`) | 4 |
| 4-35 | auth token | 32 |

### `SRCH` -- search loads

Request body (after header):

| Field | Width | Notes |
|---|---|---|
| origin | 20 | space-padded |
| destination | 20 | optional, blank if not specified |
| equipment_type | 10 | `DRY_VAN`, `REEFER`, `FLATBED` |

Response: status line `OK00` + 4-digit record count, then one line per
matching load (see record format below), then `END`.

### `DETL` -- load detail

Request body: `load_id` (10). Response: status line + one record line + `END`.

### `BOOK` -- book a load

Request body: `load_id` (10) + `mc_number` (10) + `agreed_rate` (10, 2 decimals).
Response: status line + `confirmation_id` (20) + `END`.

### Load record format (54 + variable notes)

| Field | Width |
|---|---|
| load_id | 10 |
| origin | 20 |
| destination | 20 |
| pickup_datetime (`YYYYMMDDHHmm`) | 12 |
| delivery_datetime (`YYYYMMDDHHmm`) | 12 |
| equipment_type | 10 |
| loadboard_rate (cents, zero-padded) | 10 |
| max_rate (cents, zero-padded) | 10 |
| weight | 8 |
| commodity_type | 20 |
| num_of_pieces | 6 |
| miles | 6 |
| dimensions | 20 |
| notes | 40 |

### Failure modes the adapter must tolerate

Per the brief, the system is "known to be unreliable under load": timeouts and
malformed responses occur intermittently. `tms_client.py` handles this with:

- A short socket timeout (5s) per attempt.
- Up to 2 retries with exponential backoff (0.5s, 1.5s) on timeout or connection error.
- A circuit breaker that opens after 5 consecutive failures and stays open for
  30 seconds before allowing a half-open probe, so a degraded TMS doesn't pile
  up hung calls on every concurrent voice session.
- Strict response-length/format validation; a malformed line raises a typed
  `TmsProtocolError` rather than crashing, which the REST layer converts to a
  clean `502` the workflow can branch on (e.g. "system temporarily
  unavailable, can I get a callback number" instead of dead air).
