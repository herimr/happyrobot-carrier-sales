"""Mock TMS server implementing the REAL pipe-delimited protocol.

Wire format matches the official spec:
  Request:  CMD:<cmd>|AUTH:<token>|<FIELD>:<VALUE>|...\r\n
  Response: <FIELD>:<VALUE>|...\r\n (zero or more records) + END\r\n
  Error:    ERR|CODE:<code>|MSG:<msg>\r\n

Run: python -m app.mocks.mock_tms_server
"""
from __future__ import annotations

import socketserver
from datetime import datetime, timedelta

_TOKEN = None  # set at runtime from settings to match whatever the client sends

_LOADS = [
    {
        "LOAD_ID": "LOAD000001",
        "ORIGIN": "Chicago, IL",
        "DESTINATION": "Dallas, TX",
        "PICKUP_DT": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        "DELIVERY_DT": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"),
        "EQUIPMENT": "DRY_VAN",
        "RATE": "1850.00",
        "MAX_RATE": "2100.00",
        "WEIGHT": "38000",
        "COMMODITY": "General freight",
        "PIECES": "24",
        "MILES": "925",
        "DIMENSIONS": "48x102x110",
        "NOTES": "",
    },
    {
        "LOAD_ID": "LOAD000002",
        "ORIGIN": "Chicago, IL",
        "DESTINATION": "Atlanta, GA",
        "PICKUP_DT": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        "DELIVERY_DT": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S"),
        "EQUIPMENT": "REEFER",
        "RATE": "2200.00",
        "MAX_RATE": "2450.00",
        "WEIGHT": "41000",
        "COMMODITY": "Frozen foods",
        "PIECES": "18",
        "MILES": "715",
        "DIMENSIONS": "48x102x110",
        "NOTES": "Temp -10F",
    },
]


def _kv_line(d: dict) -> str:
    return "|".join(f"{k}:{v}" for k, v in d.items()) + "\r\n"


def _err(code: str, msg: str) -> bytes:
    return f"ERR|CODE:{code}|MSG:{msg}\r\n".encode("ascii")


def _parse_request(line: str) -> dict[str, str]:
    result = {}
    for part in line.strip().split("|"):
        if ":" in part:
            k, _, v = part.partition(":")
            result[k.strip()] = v.strip()
    return result


class TmsHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(4096).decode("ascii", errors="replace")
        fields = _parse_request(raw)

        cmd = fields.get("CMD", "")
        auth = fields.get("AUTH", "")

        from app.config import settings as _settings
        expected_token = _TOKEN or _settings.tms_auth_token
        if auth != expected_token:
            self.wfile.write(_err("AUTH_FAILED", "invalid token"))
            return

        if cmd == "LOAD_QUERY":
            origin = fields.get("ORIGIN", "").lower()
            equip = fields.get("EQUIPMENT", "").lower()
            matches = [
                l for l in _LOADS
                if origin in l["ORIGIN"].lower()
                and (not equip or equip in l["EQUIPMENT"].lower())
            ]
            out = b""
            for load in matches:
                out += _kv_line(load).encode("ascii")
            out += b"END\r\n"
            self.wfile.write(out)

        elif cmd == "LOAD_GET":
            load_id = fields.get("LOAD_ID", "")
            if load_id == "LOAD999999":
                # Deliberate malformed response for resilience tests
                self.wfile.write(b"LOAD_ID:LOAD999999|TRUNCATED\r\nEND\r\n")
                return
            match = next((l for l in _LOADS if l["LOAD_ID"] == load_id), None)
            if not match:
                self.wfile.write(_err("UNKNOWN_LOAD", f"no load with id {load_id}"))
            else:
                self.wfile.write(_kv_line(match).encode("ascii") + b"END\r\n")

        elif cmd == "LOAD_BOOK":
            load_id = fields.get("LOAD_ID", "")
            mc = fields.get("MC", "")
            rate = fields.get("RATE", "")
            if not load_id or not mc or not rate:
                self.wfile.write(_err("MISSING_FIELD", "LOAD_ID, MC, and RATE required"))
                return
            match = next((l for l in _LOADS if l["LOAD_ID"] == load_id), None)
            if not match:
                self.wfile.write(_err("UNKNOWN_LOAD", f"no load with id {load_id}"))
                return
            try:
                if float(rate) > float(match["MAX_RATE"]):
                    self.wfile.write(_err("INVALID_RATE", "rate exceeds maximum"))
                    return
            except ValueError:
                self.wfile.write(_err("INVALID_RATE", "rate must be numeric"))
                return
            conf_id = f"CONF-{load_id}-{mc}"
            self.wfile.write(
                f"CONFIRMATION_ID:{conf_id}\r\nEND\r\n".encode("ascii")
            )

        elif cmd == "DEBUG_ECHO":
            self.wfile.write(f"ECHO:{raw.strip()}\r\nEND\r\n".encode("ascii"))

        else:
            self.wfile.write(_err("UNKNOWN_CMD", f"unknown command: {cmd}"))


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run(host: str = "0.0.0.0", port: int = 9999) -> None:
    with _ReusableTCPServer((host, port), TmsHandler) as server:
        print(f"[mock-tms] listening on {host}:{port}")
        server.serve_forever()


if __name__ == "__main__":
    run()
