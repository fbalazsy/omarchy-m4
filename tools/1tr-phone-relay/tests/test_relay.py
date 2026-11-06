#!/usr/bin/env python3
"""Synthetic tests for the read-only 1TR phone relay.

No phone, hotspot, Tailscale, or recovery Mac is required. Loopback only.
"""
from __future__ import annotations

import hashlib
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
REPO = SRC.parents[1]
EXPECTED_ALLOW = frozenset({
    "inventory", "sw_vers", "diskutil_list", "diskutil_list_internal",
    "apfs_list_volume_groups", "mount_list", "uname", "stop", "rejected",
})
CLIENT_OPS = EXPECTED_ALLOW - {"rejected"}
LAN_TEXT = re.compile(
    r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
)
BOOTSTRAP = SRC / "termux-bootstrap.sh"


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def load_relay(root: Path):
    os.environ["RELAY_ROOT"] = str(root)
    os.environ["RELAY_SESSION"] = "synthetic-test-session-token"
    os.environ["RELAY_TTL"] = "120"
    os.environ["RELAY_POLL"] = "1"
    os.environ["FORCE_LAN_IP"] = "127.0.0.1"
    os.environ["FORCE_TS_IP"] = "127.0.0.1"
    lan = free_port()
    mcp = free_port()
    while mcp == lan:
        mcp = free_port()
    os.environ["LAN_PORT"] = str(lan)
    os.environ["MCP_PORT"] = str(mcp)
    spec = importlib.util.spec_from_file_location("phone_relay_under_test", SRC / "phone_relay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http_get(port: int, path: str, host: str | None = None, timeout: float = 3):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Host": host} if host else {}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    status = resp.status
    conn.close()
    return status, body


def http_post(port: int, path: str, body: bytes, headers: dict, timeout: float = 3):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    status = resp.status
    conn.close()
    return status, data


def mcp_call(port: int, payload: dict):
    status, data = http_post(
        port, "/mcp", json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )
    return status, json.loads(data.decode())


class StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = (SRC / "client_template.sh").read_text(encoding="utf-8")
        cls.relay_src = (SRC / "phone_relay.py").read_text(encoding="utf-8")

    def test_tree_has_no_lan_addresses_or_keys(self):
        for path in SRC.rglob("*"):
            if not path.is_file():
                continue
            self.assertNotIn(path.name, {"PHONE-CARD.txt", "LATEST"})
            if path.name == "session" and path.parent.name == "state":
                self.fail("runtime session file is in the tree")
            if path.suffix in {".pyc"} or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertIsNone(LAN_TEXT.search(text), path.name)
            self.assertNotIn("BEGIN " + "OPENSSH PRIVATE KEY", text)
            self.assertNotIn("BEGIN " + "RSA PRIVATE KEY", text)

    def test_allowlist_is_the_fixed_inventory_set(self):
        # Imported later by the server test; parse the source so this test
        # does not depend on relay startup.
        self.assertIn("ALLOW = frozenset({", self.relay_src)
        for op in EXPECTED_ALLOW:
            self.assertIn('"%s"' % op, self.relay_src)
        self.assertIn("ENQUEUEABLE = ALLOW - {\"rejected\"}", self.relay_src)
        arms = re.findall(r"(?m)^    ([a-z0-9_]+)\)", self.client)
        self.assertEqual(set(arms), set(CLIENT_OPS))

    def test_client_commands_stay_inside_the_allowlist(self):
        forbidden = [
            r"\beval\b", r"\bexec\b", r"\bsudo\b", r"\bumount\b", r"\bbless\b",
            r"\bnvram\b", r"\basr\b", r"\bnewfs\b", r"\bmkfs\b", r"\breboot\b",
            r"\bshutdown\b",             r"diskutil[ \t]+(?!list\b|apfs[ \t]+listVolumeGroups\b)\S+",
            r"(?m)^[ \t]*mount[ \t]+\S", r"\bsh[ \t]+-c\b", r"\bbash[ \t]+-c\b",
        ]
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, self.client), pattern)
        for literal in (
            "diskutil list\n",
            "diskutil list internal\n",
            "diskutil apfs listVolumeGroups",
            "uname -a\n",
            "\n      mount\n",
        ):
            self.assertIn(literal, self.client)
        subprocess.check_call(["dash", "-n", str(SRC / "client_template.sh")])
        subprocess.check_call(["bash", "-n", str(SRC / "run-1tr-phone-relay.sh")])

    def test_published_payload_hashes(self):
        sums = SRC / "SHA256SUMS"
        self.assertTrue(sums.is_file(), "SHA256SUMS is part of the payload")
        listed = {}
        for line in sums.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, name = line.split("  ", 1)
            listed[name] = digest
        self.assertEqual(set(listed), {"phone_relay.py", "client_template.sh", "run-1tr-phone-relay.sh"})
        for name, digest in listed.items():
            self.assertEqual(hashlib.sha256((SRC / name).read_bytes()).hexdigest(), digest)

    def test_relay_has_no_shell_execution(self):
        self.assertNotIn("shell=True", self.relay_src)
        self.assertNotIn("os.system", self.relay_src)
        self.assertIsNone(re.search(r"(?m)^\s*(?:diskutil|mount|bless|nvram)\b", self.relay_src))
        self.assertEqual(self.relay_src.count("subprocess.check_output"), 1)
        self.assertEqual(self.relay_src.count("import subprocess"), 1)
        self.assertIn('["ip", "-4", "-o", "addr", "show"]', self.relay_src)
        compile(self.relay_src, str(SRC / "phone_relay.py"), "exec")


class AddressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="1tr-addr-"))
        cls.relay = load_relay(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_ifreq_parser_reads_ipv4(self):
        raw = bytearray(40)
        raw[:2] = b"lo"
        struct_mod = __import__("struct")
        socket_mod = __import__("socket")
        struct_mod.pack_into("<H", raw, 16, socket_mod.AF_INET)
        raw[20:24] = socket_mod.inet_aton("127.0.0.1")
        self.assertEqual(self.relay._parse_ifreq_buf(bytes(raw), 40), [("lo", "127.0.0.1")])
        raw[16:18] = b"\x00\x00"
        self.assertEqual(self.relay._parse_ifreq_buf(bytes(raw), 40), [])

    def test_ioctl_finds_loopback_without_netlink(self):
        original = self.relay._ipv4s_ip_command
        self.relay._ipv4s_ip_command = lambda: (_ for _ in ()).throw(OSError("netlink denied"))
        try:
            found = self.relay.ipv4s()
        finally:
            self.relay._ipv4s_ip_command = original
        self.assertEqual(self.relay.SCAN["ip"], "failed")
        self.assertTrue(any(ip == "127.0.0.1" for _iface, ip in found))
        for _iface, ip in found:
            self.assertTrue(self.relay._assigned_here(ip))

    def test_unassigned_forced_address_is_ignored(self):
        original = self.relay.ipv4s
        self.relay.ipv4s = lambda: []
        previous_lan = os.environ.get("FORCE_LAN_IP")
        previous_ts = os.environ.get("FORCE_TS_IP")
        missing_private = ".".join(["192", "168", "50", "50"])
        missing_cgnat = ".".join(["100", "64", "8", "8"])
        os.environ["FORCE_LAN_IP"] = missing_private
        os.environ["FORCE_TS_IP"] = missing_cgnat
        try:
            self.assertEqual(self.relay.lan_endpoints(), [])
            self.assertEqual(self.relay.tailscale_ips(), [])
        finally:
            self.relay.ipv4s = original
            if previous_lan is None:
                os.environ.pop("FORCE_LAN_IP", None)
            else:
                os.environ["FORCE_LAN_IP"] = previous_lan
            if previous_ts is None:
                os.environ.pop("FORCE_TS_IP", None)
            else:
                os.environ["FORCE_TS_IP"] = previous_ts

    def test_hotspot_choice_skips_cellular_and_prefers_ap(self):
        station = ".".join(["192", "168", "1", "20"])
        hotspot = ".".join(["192", "168", "43", "1"])
        cellular = ".".join(["10", "8", "0", "2"])
        self.assertEqual(
            self.relay._select_hotspot([
                ("wlan0", station),
                ("ap0", hotspot),
                ("rmnet_data0", cellular),
            ]),
            [("ap0", hotspot)],
        )
        self.assertEqual(self.relay._select_hotspot([("wlan0", station)]), [("wlan0", station)])
        self.assertEqual(self.relay._select_hotspot([("rmnet0", cellular)]), [])

    def test_tailscale_choice_rejects_carrier_nat(self):
        cgnat = ".".join(["100", "64", "8", "8"])
        self.assertEqual(self.relay._select_tailscale([("rmnet_data0", cgnat)], None), [])
        self.assertEqual(self.relay._select_tailscale([("tailscale0", cgnat)], None), [cgnat])
        self.assertEqual(self.relay._select_tailscale([], "127.0.0.1"), ["127.0.0.1"])
        self.assertEqual(self.relay._select_tailscale([], cgnat), [])

    def test_request_log_redacts_session_token(self):
        token = "synthetic-test-session-token"
        redacted = self.relay._redact_request_target(
            "GET /s/%s/bootstrap HTTP/1.1" % token
        )
        self.assertNotIn(token, redacted)
        self.assertIn("/s/redacted/bootstrap", redacted)

    def test_missing_address_card_does_not_assume_radios_are_off(self):
        original = self.relay.ipv4s
        self.relay.ipv4s = lambda: []
        previous_lan = os.environ.get("FORCE_LAN_IP")
        previous_ts = os.environ.get("FORCE_TS_IP")
        os.environ["FORCE_LAN_IP"] = ""
        os.environ["FORCE_TS_IP"] = ""
        stdout = io.StringIO()
        try:
            with __import__("contextlib").redirect_stdout(stdout):
                self.relay.write_phone_card()
        finally:
            self.relay.ipv4s = original
            if previous_lan is None:
                os.environ.pop("FORCE_LAN_IP", None)
            else:
                os.environ["FORCE_LAN_IP"] = previous_lan
            if previous_ts is None:
                os.environ.pop("FORCE_TS_IP", None)
            else:
                os.environ["FORCE_TS_IP"] = previous_ts
        text = (self.relay.STATE / "PHONE-CARD.txt").read_text(encoding="utf-8")
        self.assertIn("does not mean the hotspot is off", text)
        self.assertIn("No address was guessed", text)
        self.assertIn("official app can still deliver", text)
        self.assertIn("It is not guessed here", text)
        self.assertIn("closed before any command is read", text)
        self.assertNotIn("Paste this ONE line", text)
        self.assertNotIn("100.x", text)

    def test_mcp_accepts_only_tailnet_or_loopback(self):
        relay = self.relay
        peer = ".".join(["100", "64", "1", "2"])
        local = ".".join(["100", "64", "1", "3"])
        hotspot_dest = ".".join(["192", "168", "43", "1"])
        hotspot_peer = ".".join(["192", "168", "43", "20"])
        self.assertTrue(relay._mcp_peer_ok(local, peer))
        self.assertTrue(relay._mcp_peer_ok("127.0.0.1", "127.0.0.1"))
        self.assertFalse(relay._mcp_peer_ok(hotspot_dest, hotspot_peer))
        self.assertFalse(relay._mcp_peer_ok(local, hotspot_peer))
        self.assertFalse(relay._mcp_peer_ok(hotspot_dest, peer))
        self.assertFalse(relay._mcp_peer_ok("127.0.0.1", peer))
        self.assertFalse(relay._mcp_peer_ok("", peer))


class ClientTests(unittest.TestCase):
    def test_client_runs_only_allowlisted_commands(self):
        work = Path(tempfile.mkdtemp(prefix="1tr-client-"))
        self.addCleanup(shutil.rmtree, work, True)
        bindir = work / "bin"
        bindir.mkdir()
        log = work / "stub.log"
        log.write_text("")
        stub_env = "STUB_LOG=%s\n" % log
        (bindir / "diskutil").write_text(
            "#!/bin/sh\n%sprintf '%%s\\n' \"$*\" >> \"$STUB_LOG\"\n"
            "case \"$*\" in\n"
            "  list|list\\ internal|apfs\\ listVolumeGroups) printf 'synthetic-diskutil %%s\\n' \"$*\" ;;\n"
            "  *) echo unexpected >&2; exit 9 ;;\n"
            "esac\n" % stub_env
        )
        (bindir / "mount").write_text(
            "#!/bin/sh\n%sif [ \"$#\" -ne 0 ]; then echo unexpected >> \"$STUB_LOG\"; exit 9; fi\n"
            "echo mount >> \"$STUB_LOG\"\necho synthetic-mount\n" % stub_env
        )
        (bindir / "sw_vers").write_text(
            "#!/bin/sh\n%sif [ \"$#\" -ne 0 ]; then exit 9; fi\n"
            "echo sw_vers >> \"$STUB_LOG\"\necho synthetic-sw_vers\n" % stub_env
        )
        (bindir / "uname").write_text(
            "#!/bin/sh\n%sif [ \"$#\" -ne 1 ] || [ \"$1\" != \"-a\" ]; then echo unexpected >> \"$STUB_LOG\"; exit 9; fi\n"
            "echo uname >> \"$STUB_LOG\"\necho synthetic-uname\n" % stub_env
        )
        for name in ("diskutil", "mount", "sw_vers", "uname"):
            (bindir / name).chmod(0o755)

        posts = []
        tasks = [
            "OP uname\nID goodid\nPOLL 1\n",
            "OP diskutil_mount\nID x\nPOLL 1\n",
            "OP uname;touch pwned\nID x\nPOLL 1\n",
            "OP uname\nID bad;id\nPOLL 1;touch pwned\n",
            "OP stop\nID stop1\nPOLL 1\n",
        ]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_GET(self):  # noqa: N802
                if self.path.endswith("/task"):
                    body = (tasks.pop(0) if tasks else "OP none\nPOLL 1\n").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or "0")
                posts.append({
                    "op": self.headers.get("X-1TR-Op"),
                    "id": self.headers.get("X-1TR-Id"),
                    "body": self.rfile.read(n),
                })
                raw = b"saved\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)

        script = (SRC / "client_template.sh").read_text(encoding="utf-8")
        script = script.replace("__BASE__", "http://127.0.0.1:%d/s/synthetic" % port, 1)
        path = work / "client.sh"
        path.write_text(script)
        env = os.environ.copy()
        env["PATH"] = "%s%s%s" % (bindir, os.pathsep, env.get("PATH", ""))
        env["TMPDIR"] = str(work)
        env["STUB_LOG"] = str(log)
        proc = subprocess.Popen(
            ["dash", str(path)], cwd=work, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            out, _ = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
            self.fail("client timed out:\n%s" % out)
        self.assertEqual(proc.returncode, 0, out)
        self.assertFalse((work / "pwned").exists(), out)
        ops = [item["op"] for item in posts]
        self.assertEqual(ops[0], "inventory")
        self.assertIn(b"synthetic-diskutil list", posts[0]["body"])
        self.assertIn(b"synthetic-diskutil list internal", posts[0]["body"])
        self.assertIn(b"synthetic-diskutil apfs listVolumeGroups", posts[0]["body"])
        self.assertIn(b"synthetic-mount", posts[0]["body"])
        self.assertIn(b"synthetic-sw_vers", posts[0]["body"])
        self.assertIn("uname", ops)
        self.assertIn("rejected", ops)
        self.assertEqual(ops[-1], "stop")
        ids = [item["id"] for item in posts]
        self.assertIn("goodid", ids)
        self.assertIn("none", ids)
        self.assertNotIn("bad;id", ids)
        for item in posts:
            self.assertNotIn(";", item["id"] or "")
            self.assertNotIn("\n", item["id"] or "")
            self.assertNotIn("\r", item["id"] or "")
        logged = [line for line in log.read_text().splitlines() if line]
        self.assertEqual(logged.count("list"), 1)
        self.assertEqual(logged.count("list internal"), 1)
        self.assertEqual(logged.count("apfs listVolumeGroups"), 1)
        self.assertEqual(logged.count("mount"), 1)
        self.assertEqual(logged.count("sw_vers"), 1)
        self.assertEqual(logged.count("uname"), 2)
        self.assertNotIn("unexpected", log.read_text())


class RelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="1tr-relay-"))
        shutil.copy(SRC / "client_template.sh", cls.root / "client_template.sh")
        cls.relay = load_relay(cls.root)
        cls.relay.ipv4s = lambda: []
        cls.errors = []
        cls.thread = threading.Thread(target=cls._run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 5
        last = None
        while time.time() < deadline:
            try:
                status, body = http_get(cls.relay.LAN_PORT, "/s/%s/health" % cls.relay.SESSION)
                if status == 200 and body.startswith(b"ok "):
                    return
                last = (status, body)
            except OSError as exc:
                last = exc
            time.sleep(0.05)
        raise AssertionError("relay did not start: %r / %r" % (last, cls.errors))

    @classmethod
    def _run(cls):
        try:
            cls.relay.main()
        except Exception as exc:  # pragma: no cover - failure path
            cls.errors.append(exc)

    @classmethod
    def tearDownClass(cls):
        try:
            mcp_call(cls.relay.MCP_PORT, {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "request_stop", "arguments": {}},
            })
        except Exception:
            cls.relay.STOP.set()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_server_is_allowlist_only(self):
        relay = self.relay
        token = relay.SESSION
        self.assertEqual(relay.ALLOW, EXPECTED_ALLOW)
        self.assertEqual(relay.ENQUEUEABLE, CLIENT_OPS)
        self.assertEqual(http_get(relay.LAN_PORT, "/s/not-the-session-token/bootstrap")[0], 404)

        status, body = http_get(relay.LAN_PORT, "/s/%s/bootstrap" % token, host="203.0.113.5:%d" % relay.LAN_PORT)
        text = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('BASE="http://203.0.113.5:%d/s/%s"' % (relay.LAN_PORT, token), text)
        self.assertNotIn("pwned", text)

        status, body = http_get(
            relay.LAN_PORT, "/s/%s/bootstrap" % token,
            host='203.0.113.5:%d";touch pwned' % relay.LAN_PORT,
        )
        text = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('BASE="http://127.0.0.1:%d/s/%s"' % (relay.LAN_PORT, token), text)
        self.assertNotIn("pwned", text)
        self.assertNotIn(";", text.split('BASE="', 1)[1].split('"', 1)[0])

        self.assertEqual(http_post(
            relay.LAN_PORT, "/s/%s/enqueue" % token, b"{}",
            {"Content-Type": "application/json"},
        )[0], 404)
        self.assertEqual(http_post(
            relay.LAN_PORT, "/s/%s/result" % token, b"x",
            {"Content-Length": "nope", "X-1TR-Op": "uname", "X-1TR-Id": "1"},
        )[0], 400)
        self.assertEqual(http_post(
            relay.LAN_PORT, "/s/%s/result" % token, b"",
            {"Content-Length": "300000", "X-1TR-Op": "uname", "X-1TR-Id": "1"},
        )[0], 413)

        status, listed = mcp_call(relay.MCP_PORT, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(status, 200)
        tools = {item["name"]: item for item in listed["result"]["tools"]}
        self.assertEqual(set(tools), {"status", "list_results", "read_result", "enqueue", "request_stop"})
        self.assertEqual(tools["enqueue"]["inputSchema"]["properties"]["op"]["enum"], sorted(CLIENT_OPS))

        for op in ("bash", "diskutil mount disk0", "sh", ""):
            _status, msg = mcp_call(relay.MCP_PORT, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "enqueue", "arguments": {"op": op, "cmd": "rm -rf /"}},
            })
            self.assertTrue(msg["result"].get("isError"), msg)
        self.assertEqual(relay.TASKS.qsize(), 0)

        _status, msg = mcp_call(relay.MCP_PORT, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "enqueue", "arguments": {"op": "inventory", "cmd": "id"}},
        })
        self.assertFalse(msg["result"].get("isError"), msg)
        status, task = http_get(relay.LAN_PORT, "/s/%s/task" % token)
        self.assertEqual(status, 200)
        self.assertEqual(task.decode(), "OP inventory\nID 1\nPOLL 1\n")
        relay.TASKS.put(("diskutil mount disk0", "bad;id"))
        _status, task = http_get(relay.LAN_PORT, "/s/%s/task" % token)
        self.assertEqual(task.decode(), "OP none\nID none\nPOLL 1\n")

        status, _body = http_post(
            relay.LAN_PORT, "/s/%s/result" % token, b"synthetic inventory\n",
            {"Content-Type": "text/plain", "X-1TR-Op": "inventory", "X-1TR-Id": "../x"},
        )
        self.assertEqual(status, 200)
        names = list((self.root / "results").glob("*.txt"))
        self.assertEqual(len(names), 1)
        self.assertEqual(stat.S_IMODE(names[0].stat().st_mode), 0o600)
        self.assertNotIn("..", names[0].name)
        link = self.root / "results" / "escape.txt"
        link.symlink_to("/etc/passwd")
        _status, msg = mcp_call(relay.MCP_PORT, {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "read_result", "arguments": {"name": "escape.txt"}},
        })
        self.assertTrue(msg["result"].get("isError"), msg)
        self.assertNotIn("root:", msg["result"]["content"][0]["text"])
        _status, msg = mcp_call(relay.MCP_PORT, {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "read_result", "arguments": {"name": names[0].name}},
        })
        self.assertFalse(msg["result"].get("isError"), msg)
        self.assertIn("synthetic inventory", msg["result"]["content"][0]["text"])
        card = self.root / "state" / "PHONE-CARD.txt"
        self.assertEqual(stat.S_IMODE(card.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "state" / "session").stat().st_mode), 0o600)
        self.assertIn("http://127.0.0.1:%d/s/%s/bootstrap" % (relay.LAN_PORT, token), card.read_text())
        self.assertFalse((SRC / "state").exists())
        self.assertFalse((SRC / "results").exists())


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        if not BOOTSTRAP.is_file():
            self.skipTest("termux-bootstrap.sh is published in the pin commit")

    def test_pin_matches_payload_and_git(self):
        text = BOOTSTRAP.read_text(encoding="utf-8")
        commit = re.search(r'(?m)^PAYLOAD_COMMIT="([0-9a-f]{40})"$', text).group(1)
        hashes = {
            "phone_relay.py": re.search(r'(?m)^HASH_PY="([0-9a-f]{64})"$', text).group(1),
            "client_template.sh": re.search(r'(?m)^HASH_CLIENT="([0-9a-f]{64})"$', text).group(1),
            "run-1tr-phone-relay.sh": re.search(r'(?m)^HASH_RUN="([0-9a-f]{64})"$', text).group(1),
        }
        listed = {}
        for line in (SRC / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, name = line.split("  ", 1)
            listed[name] = digest
        self.assertEqual(listed, hashes)
        for name, digest in hashes.items():
            blob = (SRC / name).read_bytes()
            self.assertEqual(hashlib.sha256(blob).hexdigest(), digest)
            pinned = subprocess.check_output(
                ["git", "show", "%s:tools/1tr-phone-relay/%s" % (commit, name)], cwd=REPO,
            )
            self.assertEqual(hashlib.sha256(pinned).hexdigest(), digest)
            self.assertEqual(pinned, blob)
        subprocess.check_call(["dash", "-n", str(BOOTSTRAP)])
        self.assertIn("https://raw.githubusercontent.com/fbalazsy/omarchy-m4/${PAYLOAD_COMMIT}/tools/1tr-phone-relay", text)
        self.assertIn("--proto '=https'", text)
        self.assertIn("--tlsv1.2", text)
        self.assertIn("--max-redirs 0", text)
        self.assertGreaterEqual(text.count("</dev/null"), 4)
        self.assertNotIn("pkg upgrade", text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("curl | sh", text)
        self.assertIsNone(LAN_TEXT.search(text))

    def test_https_fetch_checks_hash_before_launch(self):
        work = Path(tempfile.mkdtemp(prefix="1tr-boot-"))
        self.addCleanup(shutil.rmtree, work, True)
        cert = work / "cert.pem"
        key = work / "key.pem"
        subprocess.check_call([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(cert), "-days", "1", "-nodes",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload = work / "good"
        payload.mkdir()
        for name in ("phone_relay.py", "client_template.sh", "run-1tr-phone-relay.sh"):
            shutil.copy(SRC / name, payload / name)
        bad = work / "bad"
        shutil.copytree(payload, bad)
        flipped = bytearray((bad / "phone_relay.py").read_bytes())
        flipped[0] ^= 0x01
        (bad / "phone_relay.py").write_bytes(flipped)

        def serve(directory: Path):
            class Handler(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(directory), **kwargs)

                def log_message(self, fmt, *args):
                    return

            httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cert, key)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            return httpd

        good_http = serve(payload)
        self.addCleanup(good_http.shutdown)
        script = BOOTSTRAP.read_text(encoding="utf-8")
        script = re.sub(
            r'(?m)^REPO=".*"$',
            'REPO="https://127.0.0.1:%d"' % good_http.server_address[1],
            script,
            count=1,
        )
        script_path = work / "bootstrap.sh"
        script_path.write_text(script)
        home = work / "home"
        home.mkdir()
        fakebin = work / "bin"
        fakebin.mkdir()
        pkg_log = work / "pkg.log"
        (fakebin / "pkg").write_text(
            "#!/bin/sh\nprintf '%%s\\n' \"$*\" >> \"%s\"\nexit 0\n" % pkg_log
        )
        (fakebin / "pkg").chmod(0o755)
        lan = free_port()
        mcp = free_port()
        env = os.environ.copy()
        for env_name in ("RELAY_ROOT", "RELAY_SESSION", "FORCE_TS_IP", "RELAY_POLL"):
            env.pop(env_name, None)
        env.update({
            "HOME": str(home),
            "PATH": "%s%s%s" % (fakebin, os.pathsep, env.get("PATH", "")),
            "CURL_CA_BUNDLE": str(cert),
            "SSL_CERT_FILE": str(cert),
            "LAN_PORT": str(lan),
            "MCP_PORT": str(mcp),
            "FORCE_LAN_IP": "127.0.0.1",
            "RELAY_TTL": "30",
            "DEBIAN_FRONTEND": "noninteractive",
        })
        proc = subprocess.Popen(
            ["dash", str(script_path)], cwd=work, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        captured = io.StringIO()

        def drain():
            assert proc.stdout is not None
            for line in proc.stdout:
                captured.write(line)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        deadline = time.time() + 20
        while time.time() < deadline and "lan listening" not in captured.getvalue():
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        out = captured.getvalue()
        self.assertIn("lan listening", out, out)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        reader.join(timeout=2)
        pkg_text = pkg_log.read_text(encoding="utf-8")
        self.assertIn("install -y python", pkg_text)
        self.assertIn("install -y iproute2", pkg_text)
        self.assertNotIn("upgrade", pkg_text)
        dest = home / ".local/share/1tr-phone-relay/phone_relay.py"
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), hashlib.sha256((SRC / "phone_relay.py").read_bytes()).hexdigest())
        card = home / ".local/share/1tr-phone-relay/state/PHONE-CARD.txt"
        self.assertEqual(stat.S_IMODE(card.stat().st_mode), 0o600)
        card_text = card.read_text(encoding="utf-8")
        self.assertTrue(
            "Paste this ONE line" in card_text or "Paste ONE of these lines" in card_text,
            "relay did not print a paste line",
        )
        self.assertNotIn("__BASE__", card_text)

        bad_http = serve(bad)
        self.addCleanup(bad_http.shutdown)
        bad_script = re.sub(
            r'(?m)^REPO=".*"$',
            'REPO="https://127.0.0.1:%d"' % bad_http.server_address[1],
            BOOTSTRAP.read_text(encoding="utf-8"),
            count=1,
        )
        bad_path = work / "bootstrap-bad.sh"
        bad_path.write_text(bad_script)
        bad_home = work / "bad-home"
        bad_home.mkdir()
        env["HOME"] = str(bad_home)
        env["LAN_PORT"] = str(free_port())
        env["MCP_PORT"] = str(free_port())
        done = subprocess.run(
            ["dash", str(bad_path)], cwd=work, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20,
        )
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("sha256 mismatch", done.stdout)
        self.assertNotIn("lan listening", done.stdout)
        self.assertFalse((bad_home / ".local/share/1tr-phone-relay/phone_relay.py").exists())


if __name__ == "__main__":
    unittest.main()
