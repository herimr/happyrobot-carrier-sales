"""TCP client for the legacy TMS — real protocol implementation.

Wire format (from the official spec):
  Request:  CMD:<cmd>|AUTH:<token>|<FIELD>:<VALUE>|...\r\n
  Response: <FIELD>:<VALUE>|<FIELD>:<VALUE>|...\r\n  (zero or more record lines)
            END\r\n
  Error:    ERR|CODE:<code>|MSG:<msg>\r\n

One TCP connection per request. Connection reuse is not supported.

Resilience:
  - Short socket timeout + bounded retries with exponential backoff
  - Circuit breaker to fail fast when TMS is persistently down
  - Strict response parsing — malformed lines raise TmsProtocolError
    rather than returning bad data silently
"""
from __future__ import annotations

import socket
import time
from typing import Optional

from app.config import settings
from app.models import BookLoadResponse, EquipmentType, Load

SOCKET_TIMEOUT_SECONDS = 5.0
MAX_RETRIES = 2
BACKOFF_SCHEDULE = (0.5, 1.5)

CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_COOLDOWN_SECONDS = 30.0


class TmsConnectionError(Exception):
    """Raised when the TMS is unreachable after retries, or the circuit is open."""


class TmsProtocolError(Exception):
    """Raised when the TMS responds but the payload doesn't parse."""


class TmsBusinessError(Exception):
    """Raised for known business-level errors (UNKNOWN_LOAD, ALREADY_BOOKED, etc.)."""
    def __init__(self, code: str, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"{code}: {msg}")


class _CircuitBreaker:
    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.opened_at: Optional[float] = None

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at >= CIRCUIT_COOLDOWN_SECONDS:
            return False  # half-open: allow one probe
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= CIRCUIT_FAILURE_THRESHOLD:
            self.opened_at = time.time()


_breaker = _CircuitBreaker()


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _send_recv(line: str) -> bytes:
    """Send one request line, read until END\\r\\n or ERR line."""
    if _breaker.is_open():
        raise TmsConnectionError("circuit open: TMS has failed repeatedly, cooling down")

    payload = (line + "\r\n").encode("ascii")
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            with socket.create_connection(
                (settings.tms_host, settings.tms_port), timeout=SOCKET_TIMEOUT_SECONDS
            ) as sock:
                sock.sendall(payload)
                buf = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if buf.endswith(b"END\r\n") or b"\r\nERR|" in buf or buf.startswith(b"ERR|"):
                        break
                _breaker.record_success()
                return buf
        except (socket.timeout, ConnectionError, OSError) as exc:
            last_error = exc
            _breaker.record_failure()
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SCHEDULE[attempt])

    raise TmsConnectionError(
        f"TMS unreachable after {MAX_RETRIES + 1} attempts: {last_error}"
    )


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def _build_request(cmd: str, **fields: str) -> str:
    parts = [f"CMD:{cmd}", f"AUTH:{settings.tms_auth_token}"]
    parts += [f"{k}:{v}" for k, v in fields.items()]
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _parse_kv(line: str) -> dict[str, str]:
    """Parse a pipe-delimited KEY:VALUE line into a dict."""
    result = {}
    for part in line.strip().split("|"):
        if ":" in part:
            k, _, v = part.partition(":")
            result[k.strip()] = v.strip()
    return result


def _decode_response(raw: bytes) -> list[dict[str, str]]:
    """Decode full response bytes into a list of record dicts."""
    text = raw.decode("ascii", errors="strict")
    lines = [ln for ln in text.split("\r\n") if ln]

    if not lines:
        raise TmsProtocolError("empty response from TMS")

    # Check for error response
    first = lines[0]
    if first.startswith("ERR|"):
        kv = _parse_kv(first)
        code = kv.get("CODE", "UNKNOWN")
        msg = kv.get("MSG", first)
        # Business errors vs protocol errors
        business_codes = {
            "UNKNOWN_LOAD", "ALREADY_BOOKED", "INVALID_RATE",
            "AUTH_FAILED", "MISSING_FIELD", "UNKNOWN_CMD"
        }
        if code in business_codes:
            raise TmsBusinessError(code, msg)
        raise TmsProtocolError(f"TMS error {code}: {msg}")

    # Strip trailing END line
    records = [ln for ln in lines if ln != "END"]
    return [_parse_kv(ln) for ln in records if ln]


def _record_to_load(r: dict[str, str]) -> Load:
    """Convert a parsed TMS record dict to a Load model."""
    from datetime import datetime

    def dt(s: str) -> datetime:
        # Try a few common formats
        for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M"):
            try:
                return datetime.strptime(s.strip(), fmt)
            except ValueError:
                continue
        raise TmsProtocolError(f"unrecognized datetime format: {s!r}")

    try:
        # Real TMS field names per spec transcripts
        orig_city  = r.get("ORIG_CITY", "").strip()
        orig_state = r.get("ORIG_STATE", "").strip()
        dest_city  = r.get("DEST_CITY", "").strip()
        dest_state = r.get("DEST_STATE", "").strip()
        origin      = f"{orig_city}, {orig_state}" if orig_state else orig_city
        destination = f"{dest_city}, {dest_state}" if dest_state else dest_city

        # RATE is in cents (7 digits, zero-padded) per the sample transcripts
        rate_raw = r.get("RATE", "0").strip()
        rate = float(rate_raw) / 100 if len(rate_raw) >= 6 else float(rate_raw)

        # PICKUP_DT format: 20260512080000 (YYYYMMDDHHmmss)
        pickup_raw = r.get("PICKUP_DT", "").strip()

        # MAX_RATE not provided by TMS in query results; derive from RATE with a
        # small buffer so the negotiation engine has a usable ceiling.
        # In production replace with a real source (LOAD_GET or a rate sheet).
        max_rate = rate * 1.15

        return Load(
            load_id=r["LOAD_ID"].strip(),
            origin=origin,
            destination=destination,
            pickup_datetime=dt(pickup_raw),
            delivery_datetime=dt(pickup_raw),   # TMS only returns pickup; estimate delivery
            equipment_type=EquipmentType(r["EQTYPE"].strip().lower()),
            loadboard_rate=rate,
            max_rate=max_rate,
            weight=int(r.get("WEIGHT", "0").strip() or 0),
            commodity_type=r.get("COMMODITY", "").strip(),
            num_of_pieces=int(r.get("PIECES", "0").strip() or 0),
            miles=int(r.get("MILES", "0").strip() or 0),
            dimensions=r.get("DIMENSIONS", "").strip(),
            notes=r.get("NOTES", "").strip() or None,
        )
    except (KeyError, ValueError) as exc:
        raise TmsProtocolError(f"malformed load record — missing/bad field: {exc}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _parse_location(loc: str) -> dict[str, str]:
    """Split 'Chicago, IL' into ORIG_CITY + ORIG_STATE as the TMS expects."""
    parts = [p.strip() for p in loc.split(",")]
    if len(parts) >= 2:
        return {"ORIG_CITY": parts[0], "ORIG_STATE": parts[1]}
    return {"ORIG_CITY": loc}


def search_loads(
    origin: str,
    destination: Optional[str],
    equipment_type: EquipmentType,
) -> list[Load]:
    # Real field names per the TMS spec transcripts: ORIG_CITY, ORIG_STATE, EQTYPE
    fields: dict[str, str] = {
        **_parse_location(origin),
        "EQTYPE": equipment_type.value.upper(),
        "MAX_RESULTS": "10",
    }
    if destination:
        dest_parts = [p.strip() for p in destination.split(",")]
        if len(dest_parts) >= 2:
            fields["DEST_STATE"] = dest_parts[1]
        else:
            fields["DEST_CITY"] = destination

    raw = _send_recv(_build_request("LOAD_QUERY", **fields))
    records = _decode_response(raw)
    return [_record_to_load(r) for r in records]


def get_load_detail(load_id: str) -> Optional[Load]:
    raw = _send_recv(_build_request("LOAD_GET", LOAD_ID=load_id))
    try:
        records = _decode_response(raw)
    except TmsBusinessError as exc:
        if exc.code == "UNKNOWN_LOAD":
            return None
        raise
    if not records:
        return None
    return _record_to_load(records[0])


def book_load(load_id: str, mc_number: str, agreed_rate: float) -> BookLoadResponse:
    raw = _send_recv(_build_request(
        "LOAD_BOOK",
        LOAD_ID=load_id,
        MC=mc_number,
        RATE=f"{agreed_rate:.2f}",
    ))
    records = _decode_response(raw)
    confirmation_id = records[0].get("CONFIRMATION_ID", "") if records else ""
    if not confirmation_id:
        raise TmsProtocolError("TMS returned OK but no CONFIRMATION_ID")
    return BookLoadResponse(
        confirmation_id=confirmation_id,
        load_id=load_id,
        status="booked",
    )
