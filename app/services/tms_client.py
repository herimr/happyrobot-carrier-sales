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
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M"):
            try:
                return datetime.strptime(s.strip(), fmt)
            except ValueError:
                continue
        raise TmsProtocolError(f"unrecognized datetime format: {s!r}")

    try:
        return Load(
            load_id=r["LOAD_ID"],
            origin=r["ORIGIN"],
            destination=r["DESTINATION"],
            pickup_datetime=dt(r["PICKUP_DT"]),
            delivery_datetime=dt(r["DELIVERY_DT"]),
            equipment_type=EquipmentType(r["EQUIPMENT"].lower()),
            loadboard_rate=float(r["RATE"]),
            max_rate=float(r["MAX_RATE"]),
            weight=int(r.get("WEIGHT", 0)),
            commodity_type=r.get("COMMODITY", ""),
            num_of_pieces=int(r.get("PIECES", 0)),
            miles=int(r.get("MILES", 0)),
            dimensions=r.get("DIMENSIONS", ""),
            notes=r.get("NOTES") or None,
        )
    except (KeyError, ValueError) as exc:
        raise TmsProtocolError(f"malformed load record — missing/bad field: {exc}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_loads(
    origin: str,
    destination: Optional[str],
    equipment_type: EquipmentType,
) -> list[Load]:

    possible_requests = [
        _build_request(
            "LOAD_QUERY",
            ORIGIN=origin,
            EQUIPMENT=equipment_type.value.upper(),
        ),

        _build_request(
            "LOAD_QUERY",
            ORIGIN_CITY=origin,
            EQUIPMENT=equipment_type.value.upper(),
        ),

        _build_request(
            "LOAD_QUERY",
            ORIGIN=origin,
        ),

        _build_request(
            "LOAD_QUERY",
            EQUIPMENT=equipment_type.value.upper(),
        ),
    ]

    for req in possible_requests:
        print("TRYING:", req)

        raw = _send_recv(req)

        print("RESPONSE:", raw)

        try:
            records = _decode_response(raw)
            return [_record_to_load(r) for r in records]
        except TmsBusinessError:
            continue

    raise TmsBusinessError(
        "SEARCH_FAILED",
        "Could not determine required TMS search fields"
    )


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

    request = _build_request(
        "LOAD_BOOK",
        LOAD_ID=load_id,
        MC=mc_number,
        RATE=f"{agreed_rate:.2f}",
    )

    print(f"TMS REQUEST: {request}")

    raw = _send_recv(request)

    print(f"TMS RESPONSE: {raw!r}")

    records = _decode_response(raw)

    confirmation_id = records[0].get("CONFIRMATION_ID", "") if records else ""

    if not confirmation_id:
        raise TmsProtocolError("TMS returned OK but no CONFIRMATION_ID")

    return BookLoadResponse(
        confirmation_id=confirmation_id,
        load_id=load_id,
        status="booked",
    )
