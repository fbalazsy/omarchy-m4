#!/usr/bin/env python3
"""Phone-side 1TR LAN relay + tailnet MCP. No public tunnel. Stdlib only.

The Mac in recovery may run only the fixed read-only inventory allowlist
shared with client_template.sh. There is no shell tool, no disk mount, and
no boot or ESP write. The session token is generated on the phone and is
never part of the published tree.
"""
from __future__ import annotations

import ctypes
import fcntl
import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(os.environ["RELAY_ROOT"]).expanduser().resolve() if os.environ.get("RELAY_ROOT") else Path(__file__).resolve().parent
STATE = ROOT / "state"
RESULTS = ROOT / "results"
TEMPLATE = ROOT / "client_template.sh"

# Fixed read-only Mac 1TR inventory allowlist. Keep in lockstep with
# client_template.sh. "rejected" is a stored report only, never a Mac command.
ALLOW = frozenset({
    "inventory", "sw_vers", "diskutil_list", "diskutil_list_internal",
    "apfs_list_volume_groups", "mount_list", "uname", "stop", "rejected",
})
ENQUEUEABLE = ALLOW - {"rejected"}
MAX_BODY = 256 * 1024
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
_SESSION_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_IPV4_RE = re.compile(r"(?:\d{1,3}\.){3}\d{1,3}")
_IFACE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,32}")
# Android hotspot interface names vary by vendor. wlan0 is usually the
# station radio, so it is not preferred over an ap/swlan interface.
_AP_RE = re.compile(
    r"^(?:ap\d+|swlan\d+|softap\d*|wlan[1-9]\d*|wifi-ap\d*|ap_br_wlan\d*|tether\d*|rndis\d+|bt-pan\d*)$",
    re.I,
)
_CELL_RE = re.compile(r"^(?:rmnet|ccmni|wwan|pdp|v4-rmnet)", re.I)
# Carrier NAT uses the same 100.64.0.0/10 block as Tailscale. Only a tun-like
# interface is treated as the tailnet. A cellular name must never host MCP.
_TS_IFACE_RE = re.compile(r"^(?:tailscale\d*|tun\d*|wg\d*)$", re.I)
_SIOCGIFCONF = 0x8912
_SIOCGIFADDR = 0x8915
_SIOCGIFFLAGS = 0x8913
_IFF_UP = 0x1
SCAN = {"ioctl": 0, "ip": "skip"}
_CMD_RE = re.compile(
    r"^curl -fsSL --max-time 60 "
    r"http://(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}/s/[A-Za-z0-9_-]+/bootstrap "
    r"-o /tmp/1tr-boot\.sh && sh /tmp/1tr-boot\.sh$"
)


def _port(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    if not re.fullmatch(r"\d{1,5}", raw or ""):
        raise SystemExit("%s must be a TCP port" % name)
    value = int(raw)
    if not 1 <= value <= 65535:
        raise SystemExit("%s must be a TCP port" % name)
    return value


def _ttl() -> int:
    raw = os.environ.get("RELAY_TTL", str(2 * 60 * 60))
    if not re.fullmatch(r"\d{1,7}", raw or ""):
        return 2 * 60 * 60
    value = int(raw)
    return value if value >= 1 else 2 * 60 * 60


def _poll() -> int:
    raw = os.environ.get("RELAY_POLL", "5")
    if not re.fullmatch(r"\d{1,2}", raw or ""):
        return 5
    value = int(raw)
    return value if 1 <= value <= 60 else 5


def _session_token() -> str:
    raw = os.environ.get("RELAY_SESSION") or secrets.token_urlsafe(32)
    if not _SESSION_RE.fullmatch(raw):
        raise SystemExit("RELAY_SESSION must be 16-128 characters from [A-Za-z0-9_-]")
    return raw


TTL_SEC = _ttl()
POLL_SEC = _poll()
LAN_PORT = _port("LAN_PORT", "8780")
MCP_PORT = _port("MCP_PORT", "8781")
SESSION = _session_token()
STARTED = time.time()
TASKS: queue.Queue[tuple[str, str]] = queue.Queue()
TASK_ID = 0
LOCK = threading.Lock()
HITS: dict[str, list[float]] = {"lan": [], "mcp": []}
STOP = threading.Event()
CLIENT_SEEN = {"bootstrap": 0, "results": 0, "last_op": None}


def _clean_ipv4(raw: str) -> str | None:
    text = (raw or "").strip()
    if not _IPV4_RE.fullmatch(text):
        return None
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    if not isinstance(addr, ipaddress.IPv4Address):
        return None
    return str(addr)


def _safe_token(value: str) -> str:
    if _TOKEN_RE.fullmatch(value or ""):
        return value
    return "none"


def _iface_label(name: str) -> str:
    if _IFACE_RE.fullmatch(name or ""):
        return name
    return "net"


def _write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _write_text(path: Path, text: str, mode: int = 0o600) -> None:
    _write_bytes(path, text.encode("utf-8"), mode)


def expired() -> bool:
    return (time.time() - STARTED) > TTL_SEC or (STATE / "STOP").exists()


def rate_ok(bucket: str) -> bool:
    now = time.time()
    with LOCK:
        xs = HITS[bucket]
        while xs and now - xs[0] > 60:
            xs.pop(0)
        if len(xs) >= 40:
            return False
        xs.append(now)
        return True


def _ifreq_size() -> int:
    # On 64-bit Linux, struct ifmap makes ifreq 40 bytes. On 32-bit it is 32.
    return 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32


def _parse_ifreq_buf(raw: bytes, size: int) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if size < 24 or len(raw) % size != 0:
        return found
    for off in range(0, len(raw), size):
        name = raw[off:off + 16].split(b"\0", 1)[0].decode("ascii", "ignore")
        if not _IFACE_RE.fullmatch(name):
            continue
        family = struct.unpack_from("<H", raw, off + 16)[0]
        if family != socket.AF_INET:
            continue
        ip = _clean_ipv4(socket.inet_ntoa(raw[off + 20:off + 24]))
        if ip:
            found.append((name, ip))
    return found


def _assigned_here(ip: str) -> bool:
    """True only when this process can bind the address. Never invent one."""
    cleaned = _clean_ipv4(ip)
    if cleaned is None:
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((cleaned, 0))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _iface_up(sock: socket.socket, name: str, size: int) -> bool:
    buf = bytearray(size)
    encoded = name.encode()[:15]
    buf[:len(encoded)] = encoded
    try:
        fcntl.ioctl(sock.fileno(), _SIOCGIFFLAGS, buf, True)
    except OSError:
        return True
    flags = struct.unpack_from("<H", buf, 16)[0]
    return bool(flags & _IFF_UP)


def _ipv4_on_iface(sock: socket.socket, name: str, size: int) -> str | None:
    if not _IFACE_RE.fullmatch(name):
        return None
    buf = bytearray(size)
    encoded = name.encode()[:15]
    buf[:len(encoded)] = encoded
    try:
        fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, buf, True)
    except OSError:
        return None
    family = struct.unpack_from("<H", buf, 16)[0]
    if family != socket.AF_INET:
        return None
    return _clean_ipv4(socket.inet_ntoa(buf[20:24]))


def _ipv4s_ifconf(sock: socket.socket) -> list[tuple[str, str]]:
    size = _ifreq_size()
    raw_buf = ctypes.create_string_buffer(size * 64)

    class _IfConf(ctypes.Structure):
        _fields_ = [("ifc_len", ctypes.c_int), ("ifc_buf", ctypes.c_void_p)]

    conf = _IfConf(size * 64, ctypes.cast(raw_buf, ctypes.c_void_p))
    try:
        fcntl.ioctl(sock.fileno(), _SIOCGIFCONF, conf)
    except OSError:
        return []
    return _parse_ifreq_buf(raw_buf.raw[:max(conf.ifc_len, 0)], size)


def _ipv4s_ioctl() -> list[tuple[str, str]]:
    """Read IPv4 addresses with SIOCGIFADDR.

    Android 13 denies Termux the netlink socket that `ip` needs
    (`Cannot bind netlink socket: Permission denied`) and hides /proc/net.
    ioctl is the path `ifconfig` still uses for addresses that exist in
    this app's network namespace.
    """
    found: list[tuple[str, str]] = []
    size = _ifreq_size()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        names: list[str] = []
        try:
            names = sorted(os.listdir("/sys/class/net"))
        except OSError:
            names = []
        if not names:
            names = [iface for iface, _ip in _ipv4s_ifconf(sock)]
        seen: set[tuple[str, str]] = set()
        for name in names:
            ip = _ipv4_on_iface(sock, name, size)
            if not ip or not _iface_up(sock, name, size):
                continue
            row = (_iface_label(name), ip)
            if row not in seen:
                seen.add(row)
                found.append(row)
        if not found:
            for iface, ip in _ipv4s_ifconf(sock):
                row = (_iface_label(iface), ip)
                if row not in seen:
                    seen.add(row)
                    found.append(row)
    finally:
        sock.close()
    return found


def _ipv4s_ip_command() -> list[tuple[str, str]]:
    import subprocess
    out = subprocess.check_output(
        ["ip", "-4", "-o", "addr", "show"], text=True, stderr=subprocess.DEVNULL
    )
    found: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        ip = _clean_ipv4(parts[3].split("/")[0])
        if ip:
            found.append((_iface_label(parts[1]), ip))
    return found


def ipv4s() -> list[tuple[str, str]]:
    """Return (iface, ip) for IPv4 addresses visible inside this process."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(rows: list[tuple[str, str]]) -> None:
        for iface, ip in rows:
            row = (_iface_label(iface), ip)
            if row[1] and row not in seen:
                seen.add(row)
                found.append(row)

    ioctl_rows = _ipv4s_ioctl()
    SCAN["ioctl"] = len(ioctl_rows)
    add(ioctl_rows)
    try:
        add(_ipv4s_ip_command())
        SCAN["ip"] = "ok"
    except Exception:
        SCAN["ip"] = "failed"
    return found


def _select_hotspot(found: list[tuple[str, str]]) -> list[tuple[str, str]]:
    preferred = [(iface, ip) for iface, ip in found if _AP_RE.fullmatch(iface)]
    if preferred:
        return preferred
    private = [
        (iface, ip) for iface, ip in found
        if classify_ip(ip) == "private" and not _CELL_RE.match(iface)
    ]
    if private:
        return private
    return [(iface, ip) for iface, ip in found if classify_ip(ip) == "loopback"]


def _select_tailscale(found: list[tuple[str, str]], forced: str | None) -> list[str]:
    ips = [
        ip for iface, ip in found
        if classify_ip(ip) == "tailscale" and _TS_IFACE_RE.fullmatch(iface)
    ]
    if forced and _assigned_here(forced):
        kind = classify_ip(forced)
        if kind == "loopback":
            ips.insert(0, forced)
        elif kind == "tailscale" and forced in ips:
            ips = [forced] + [ip for ip in ips if ip != forced]
    return list(dict.fromkeys(ips))


def classify_ip(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "other"
    if addr.is_loopback:
        return "loopback"
    if addr in ipaddress.ip_network("100.64.0.0/10"):
        return "tailscale"
    if addr.is_private and not addr.is_link_local:
        return "private"
    return "other"


def lan_endpoints() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for iface, ip in ipv4s():
        if classify_ip(ip) != "private" or _CELL_RE.match(iface) or ip in seen:
            continue
        seen.add(ip)
        found.append((iface, ip))
    extra = _clean_ipv4(os.environ.get("FORCE_LAN_IP") or "")
    if (
        extra
        and extra not in seen
        and _assigned_here(extra)
        and classify_ip(extra) in {"private", "loopback"}
    ):
        found.insert(0, ("forced", extra))
    return _select_hotspot(found)


def hotspot_ips() -> list[str]:
    return [ip for _, ip in lan_endpoints()]


def tailscale_ips() -> list[str]:
    # A forced address is accepted only when this process can bind it, and
    # only for loopback or a CGNAT address already seen on a tun-like iface.
    # An address typed from the official app is not assigned inside Termux.
    return _select_tailscale(ipv4s(), _clean_ipv4(os.environ.get("FORCE_TS_IP") or ""))


def public_base(host_header: str) -> str:
    """Build the Mac callback URL from a numeric host only.

    The recovery script quotes this value. A Host header is reflected only
    when it is already an IPv4 address and a port; anything else falls back
    to an address this phone detected. The session token is url-safe.
    """
    host = (host_header or "").strip().split("/")[0].strip()
    ip = None
    port = None
    if host.count(":") == 1:
        raw_ip, raw_port = host.split(":")
        if raw_port.isdigit() and 1 <= int(raw_port) <= 65535:
            ip = _clean_ipv4(raw_ip)
            port = str(int(raw_port))
    elif ":" not in host:
        ip = _clean_ipv4(host)
        port = str(LAN_PORT)
    if ip is None or port is None:
        hips = hotspot_ips()
        ip = hips[0] if hips else "127.0.0.1"
        port = str(LAN_PORT)
    return "http://%s:%s/s/%s" % (ip, port, SESSION)


def save_result(op: str, tid: str, body: bytes) -> Path:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe_op = "".join(c if c.isalnum() or c in "-_" else "_" for c in op)[:40]
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in tid)[:40]
    path = RESULTS / ("%s-%s-%s.txt" % (ts, safe_op, safe_id))
    # Refuse a crafted id that escapes the results directory.
    if path.resolve().parent != RESULTS.resolve():
        raise ValueError("invalid result path")
    _write_bytes(path, body, 0o600)
    meta = path.with_suffix(".json")
    _write_text(meta, json.dumps({
        "op": op, "id": tid, "bytes": len(body),
        "path": path.name, "received_unix": time.time(),
    }, indent=2) + "\n", 0o600)
    _write_text(RESULTS / "LATEST", path.name + "\n", 0o600)
    with LOCK:
        CLIENT_SEEN["results"] += 1
        CLIENT_SEEN["last_op"] = op
    return path


def enqueue(op: str) -> str:
    global TASK_ID
    if op not in ENQUEUEABLE:
        raise ValueError("op not allowlisted")
    with LOCK:
        TASK_ID += 1
        tid = str(TASK_ID)
        TASKS.put((op, tid))
    return tid


def _content_length(handler: BaseHTTPRequestHandler, limit: int) -> int | None:
    raw = handler.headers.get("Content-Length")
    if raw is None or raw.strip() == "":
        return 0
    if not re.fullmatch(r"\d+", raw.strip()):
        return None
    n = int(raw)
    if n > limit:
        return None
    return n


def _redact_request_target(requestline: str) -> str:
    parts = requestline.split(" ")
    if len(parts) < 2:
        return requestline
    segments = parts[1].split("/")
    if len(segments) >= 3 and segments[1] == "s" and segments[2]:
        segments[2] = "redacted"
        parts[1] = "/".join(segments)
    return " ".join(parts)


class _Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    bucket = "lan"

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        self.log_message('"%s" %s', _redact_request_target(self.requestline), code)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s %s - %s\n" % (self.server.role, self.log_date_time_string(), fmt % args))

    def _deny(self, code: int = 404) -> None:
        body = b"not found\n"
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, body: bytes, ctype: str = "text/plain; charset=utf-8") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)


class LanHandler(_Base):
    bucket = "lan"

    def _match(self) -> str | None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) != 3 or parts[0] != "s":
            return None
        token, action = parts[1], parts[2]
        if len(token) != len(SESSION) or not hmac.compare_digest(token, SESSION):
            return None
        return action

    def do_GET(self) -> None:  # noqa: N802
        if expired():
            self._deny(410)
            STOP.set()
            return
        action = self._match()
        if action is None:
            self._deny(404)
            return
        if not rate_ok("lan"):
            self._deny(429)
            return
        if action == "health":
            age = int(time.time() - STARTED)
            self._ok(("ok ttl_remaining=%s\n" % (TTL_SEC - age)).encode())
            return
        if action == "bootstrap":
            base = public_base(self.headers.get("Host") or "")
            text = TEMPLATE.read_text(encoding="utf-8").replace("__BASE__", base, 1)
            if "__BASE__" not in text or base not in text:
                self._deny(500)
                return
            with LOCK:
                CLIENT_SEEN["bootstrap"] += 1
            self._ok(text.encode(), "text/x-shellscript; charset=utf-8")
            return
        if action == "task":
            try:
                op, tid = TASKS.get_nowait()
            except queue.Empty:
                op, tid = "none", "none"
            if op not in ENQUEUEABLE:
                op = "none"
            body = ("OP %s\nID %s\nPOLL %s\n" % (_safe_token(op) if op != "none" else "none", _safe_token(tid), POLL_SEC)).encode()
            self._ok(body)
            return
        self._deny(404)

    def do_POST(self) -> None:  # noqa: N802
        if expired():
            self._deny(410)
            STOP.set()
            return
        action = self._match()
        # No enqueue on the LAN port — fail closed.
        if action != "result":
            self._deny(404)
            return
        if not rate_ok("lan"):
            self._deny(429)
            return
        n = _content_length(self, MAX_BODY)
        if n is None:
            raw_len = (self.headers.get("Content-Length") or "").strip()
            self._deny(413 if raw_len.isdigit() else 400)
            return
        body = self.rfile.read(n) if n else b""
        op = _safe_token((self.headers.get("X-1TR-Op") or "").strip())
        tid = _safe_token((self.headers.get("X-1TR-Id") or "").strip())
        if op not in ALLOW:
            self._deny(400)
            return
        try:
            save_result(op, tid, body)
        except ValueError:
            self._deny(400)
            return
        self._ok(b"saved\n")


def mcp_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def mcp_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


TOOLS = [
    {"name": "status", "description": "Relay TTL, seen bootstrap/results, pending queue size.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_results", "description": "List stored diagnostic result files (names and sizes only).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "read_result", "description": "Read one stored result by file name from list_results.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "enqueue", "description": "Queue one allowlisted read-only 1TR operation.",
     "inputSchema": {"type": "object", "properties": {
         "op": {"type": "string", "enum": sorted(ENQUEUEABLE)}
     }, "required": ["op"]}},
    {"name": "request_stop", "description": "Ask the 1TR client to exit and stop the relay.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def handle_mcp(msg: dict) -> dict | None:
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        return mcp_result(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "1tr-phone-relay", "version": "1"},
        })
    if method == "notifications/initialized" or method == "initialized":
        return None if req_id is None else mcp_result(req_id, {})
    if method == "ping":
        return mcp_result(req_id, {})
    if method == "tools/list":
        return mcp_result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            text = call_tool(name, args)
            return mcp_result(req_id, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            return mcp_result(req_id, {
                "content": [{"type": "text", "text": "error: %s" % e}],
                "isError": True,
            })
    if req_id is None:
        return None
    return mcp_error(req_id, -32601, "unknown method %s" % method)


def call_tool(name: str, args: dict) -> str:
    if name == "status":
        age = int(time.time() - STARTED)
        return json.dumps({
            "ttl_remaining_sec": max(0, TTL_SEC - age),
            "bootstrap_fetches": CLIENT_SEEN["bootstrap"],
            "results_stored": CLIENT_SEEN["results"],
            "last_op": CLIENT_SEEN["last_op"],
            "pending_tasks": TASKS.qsize(),
            "lan_port": LAN_PORT,
            "mcp_port": MCP_PORT,
            "hotspot_ips": hotspot_ips(),
            "tailscale_ips": tailscale_ips(),
        }, indent=2)
    if name == "list_results":
        items = []
        if RESULTS.is_dir():
            for p in sorted(RESULTS.glob("*.txt")):
                if p.is_file() and not p.is_symlink():
                    items.append({"name": p.name, "bytes": p.stat().st_size})
        return json.dumps(items, indent=2)
    if name == "read_result":
        raw = str(args.get("name") or "")
        if "/" in raw or "\\" in raw or raw.startswith(".") or ".." in raw or not raw.endswith(".txt") or raw != Path(raw).name:
            raise ValueError("invalid name")
        candidate = RESULTS / raw
        if candidate.is_symlink():
            raise ValueError("not found")
        path = candidate.resolve()
        if path.parent != RESULTS.resolve() or not path.is_file():
            raise ValueError("not found")
        data = path.read_bytes()
        if len(data) > MAX_BODY:
            raise ValueError("too large")
        return data.decode("utf-8", errors="replace")
    if name == "enqueue":
        # Only the allowlisted op is read. Any other argument is ignored.
        op = str(args.get("op") or "")
        tid = enqueue(op)
        return json.dumps({"queued": op, "id": tid})
    if name == "request_stop":
        tid = enqueue("stop")
        _write_text(STATE / "STOP", "stop\n", 0o600)
        STOP.set()
        return json.dumps({"queued": "stop", "id": tid})
    raise ValueError("unknown tool")


class McpHandler(_Base):
    bucket = "mcp"

    def do_GET(self) -> None:  # noqa: N802
        if expired():
            self._deny(410)
            return
        path = urlparse(self.path).path
        if path in ("/mcp", "/mcp/", "/health"):
            self._ok(b"1tr-phone-relay mcp\n")
            return
        self._deny(404)

    def do_POST(self) -> None:  # noqa: N802
        if expired():
            self._deny(410)
            STOP.set()
            return
        if not rate_ok("mcp"):
            self._deny(429)
            return
        path = urlparse(self.path).path
        if path not in ("/mcp", "/mcp/"):
            self._deny(404)
            return
        n = _content_length(self, 64 * 1024)
        if n is None:
            self._deny(400)
            return
        raw = self.rfile.read(n) if n else b""
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(mcp_error(None, -32700, "parse error"), 400)
            return
        if isinstance(msg, list):
            out = []
            for item in msg:
                r = handle_mcp(item) if isinstance(item, dict) else mcp_error(None, -32600, "invalid")
                if r is not None:
                    out.append(r)
            self._json(out)
            return
        if not isinstance(msg, dict):
            self._json(mcp_error(None, -32600, "invalid"), 400)
            return
        r = handle_mcp(msg)
        if r is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        self._json(r)


class RoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, role: str):
        super().__init__(addr, handler)
        self.role = role


def serve(httpd: RoleServer) -> None:
    httpd.timeout = 0.5
    try:
        while not STOP.is_set() and not expired():
            httpd.handle_request()
    finally:
        httpd.server_close()


def _paste_line(ip: str) -> str:
    cmd = (
        "curl -fsSL --max-time 60 http://%s:%s/s/%s/bootstrap -o /tmp/1tr-boot.sh && sh /tmp/1tr-boot.sh"
        % (ip, LAN_PORT, SESSION)
    )
    if not _CMD_RE.fullmatch(cmd):
        raise RuntimeError("refusing to print an unsafe 1TR command")
    return cmd


def write_phone_card() -> None:
    STATE.mkdir(mode=0o700, exist_ok=True)
    endpoints = lan_endpoints()
    tips = tailscale_ips()
    lines = []
    lines.append("=== 1TR phone relay (shown only on this phone) ===")
    lines.append("session ttl %ss  lan :%s  mcp :%s" % (TTL_SEC, LAN_PORT, MCP_PORT))
    if len(endpoints) == 1:
        _iface, ip = endpoints[0]
        lines.append("")
        lines.append("Paste this ONE line in 1TR Terminal:")
        lines.append(_paste_line(ip))
    elif endpoints:
        lines.append("")
        lines.append("Paste ONE of these lines in 1TR Terminal.")
        lines.append("Use the hotspot interface (names vary: ap0, swlan0, wlan1).")
        for iface, ip in endpoints:
            lines.append("%s" % _iface_label(iface))
            lines.append(_paste_line(ip))
    else:
        lines.append("No private IPv4 address is visible to Termux.")
        lines.append("That does not mean the hotspot is off. Android often hides tether addresses from apps.")
        lines.append("The relay is listening. This card refreshes if an address becomes visible. No address was guessed.")
    lines.append("")
    if tips:
        lines.append("MCP URL for the Tailscale plugin (tailnet only, no extra bearer):")
        lines.append("http://%s:%s/mcp" % (tips[0], MCP_PORT))
    else:
        lines.append("No Tailscale address is assigned inside Termux.")
        lines.append("The official app can be connected and still be invisible here.")
        lines.append("Android does not show that VPN to other apps, and it does not deliver inbound tailnet connections to Termux.")
        lines.append("MCP is not started and is not listening on the hotspot.")
    lines.append("Do not enable allow-hotspot-clients to use the VPN.")
    lines.append("Do not copy this card into chat or a public URL.")
    text = "\n".join(lines) + "\n"
    _write_text(STATE / "PHONE-CARD.txt", text, 0o600)
    sys.stdout.write(text)
    sys.stdout.flush()


def main() -> int:
    global STARTED
    STARTED = time.time()
    STATE.mkdir(mode=0o700, exist_ok=True)
    RESULTS.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    os.chmod(RESULTS, 0o700)
    _write_text(STATE / "session", SESSION + "\n", 0o600)
    if not TEMPLATE.is_file():
        print("client template missing", flush=True)
        return 1
    write_phone_card()

    servers = []
    # LAN listens on every interface so the hotspot address can accept the Mac.
    # Enqueue is not exposed here. The session token is required.
    lan = RoleServer(("0.0.0.0", LAN_PORT), LanHandler, "lan")
    servers.append(lan)
    threading.Thread(target=serve, args=(lan,), daemon=True).start()

    print("address scan: ioctl=%s ip=%s" % (SCAN["ioctl"], SCAN["ip"]), flush=True)
    tips = tailscale_ips()
    mcp_failed: set[str] = set()
    if tips:
        try:
            mcp = RoleServer((tips[0], MCP_PORT), McpHandler, "mcp")
            servers.append(mcp)
            threading.Thread(target=serve, args=(mcp,), daemon=True).start()
            print("mcp listening on %s:%s" % (tips[0], MCP_PORT), flush=True)
        except OSError as e:
            mcp_failed.add(tips[0])
            print("mcp bind failed (%s); not listening on all interfaces" % e, flush=True)
            tips = []
    else:
        print("mcp not started (no tailscale address assigned in Termux)", flush=True)

    print("lan listening on 0.0.0.0:%s" % LAN_PORT, flush=True)
    printed_hotspot = bool(hotspot_ips())
    try:
        while not STOP.is_set() and not expired():
            time.sleep(0.4)
            if not printed_hotspot and hotspot_ips():
                printed_hotspot = True
                write_phone_card()
                print("hotspot address appeared; card refreshed", flush=True)
            if not any(getattr(s, "role", "") == "mcp" for s in servers):
                candidates = [ip for ip in tailscale_ips() if ip not in mcp_failed]
                if not candidates:
                    continue
                try:
                    mcp = RoleServer((candidates[0], MCP_PORT), McpHandler, "mcp")
                    servers.append(mcp)
                    threading.Thread(target=serve, args=(mcp,), daemon=True).start()
                    write_phone_card()
                    print("mcp late-start on %s:%s" % (candidates[0], MCP_PORT), flush=True)
                except OSError as e:
                    mcp_failed.add(candidates[0])
                    print("mcp bind failed (%s); LAN relay keeps running" % e, flush=True)
    finally:
        STOP.set()
        print("relay stopping", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
