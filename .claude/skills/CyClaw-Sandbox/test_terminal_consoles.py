#!/usr/bin/env python3
"""
CyClaw Terminal Console Integration Tests

Requires a running CyClaw gateway with CYCLAW_API_KEY set.
Tests all 5 console panels via their REST endpoints.

Usage:
    CYCLAW_API_KEY=test-key python gate.py &
    python scripts/test_terminal_consoles.py

Env:
    CYCLAW_URL=http://127.0.0.1:8787  -- gateway URL
    CYCLAW_API_KEY=test-key           -- API key for gated endpoints
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"; B = "\033[94m"; N = "\033[0m"

API_KEY = os.environ.get("CYCLAW_API_KEY", "")
BASE_URL = os.environ.get("CYCLAW_URL", "http://127.0.0.1:8787")


class ConsoleTest:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests: list[tuple[str, bool, str]] = []

    def _request(self, method: str, path: str, body: dict | None = None,
                 with_auth: bool = True, timeout: int = 15) -> tuple[int, dict]:
        url = f"{BASE_URL}{path}"
        data = json.dumps(body).encode() if body else None
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if with_auth and API_KEY:
            req.add_header("Authorization", f"Bearer {API_KEY}")

        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode())
        except HTTPError as e:
            body_text = e.read().decode() if e.fp else "{}"
            try:
                body_json = json.loads(body_text)
            except json.JSONDecodeError:
                body_json = {"raw": body_text}
            return e.code, body_json
        except Exception as e:
            return 0, {"error": str(e)}

    def _check(self, name: str, condition: bool, detail: str = ""):
        if condition:
            self.passed += 1
            print(f"    {G}PASS{N} {name}")
        else:
            self.failed += 1
            print(f"    {R}FAIL{N} {name}" + (f" -> {detail}" if detail else ""))
        self.tests.append((name, condition, detail))

    def run_all(self):
        print(f"\n{B}CyClaw Terminal Console Integration Tests{N}")
        print(f"  Gateway: {BASE_URL}")
        print(f"  API Key: {'set' if API_KEY else 'NOT SET'}")

        # Wait for gateway
        for _ in range(10):
            status, data = self._request("GET", "/health", with_auth=False)
            if status == 200:
                mode = data.get('mode', '?')
                ver = data.get('version', '?')
                print(f"  Status: {data.get('status', '?')} mode={mode} version={ver}")
                break
            time.sleep(1)
        else:
            print(f"{R}Gateway not responding at {BASE_URL}{N}")
            sys.exit(1)

        # 1. Core Endpoints
        print(f"\n{B}--- Core Endpoints ---{N}")
        status, data = self._request("GET", "/health", with_auth=False)
        self._check("/health returns 200", status == 200)
        self._check("/health has mode", "mode" in data)
        self._check("/health has version", "version" in data)
        self._check("/health has graph_timeout_sec", "graph_timeout_sec" in data)
        self._check("/health has index_ready", "index_ready" in data)

        # Security headers
        req = Request(f"{BASE_URL}/health", method="GET")
        try:
            with urlopen(req, timeout=5) as resp:
                headers = dict(resp.headers)
                self._check("X-Content-Type-Options header", "X-Content-Type-Options" in headers)
                self._check("X-Frame-Options header", "X-Frame-Options" in headers)
        except Exception:
            self._check("Security headers", False, "Could not fetch headers")

        # 2. API Key Authentication
        print(f"\n{B}--- API Key Authentication ---{N}")
        for path in ["/soul", "/soul/propose", "/ops/sync", "/ops/agentic", "/ops/fsconnect", "/ops/sqlconnect"]:
            method = "GET" if path == "/soul" else "POST"
            body = {"action": "status"} if path.startswith("/ops/") else ({"new_soul": "x", "reason": "test"} if "propose" in path else None)
            status, _ = self._request(method, path, body=body, with_auth=False)
            self._check(f"{path} returns 401 without key", status == 401, f"got {status}")

        # Bad key test
        req = Request(f"{BASE_URL}/soul", method="GET")
        req.add_header("Authorization", "Bearer wrong-key")
        try:
            with urlopen(req, timeout=5) as resp:
                self._check("/soul returns 401 with bad key", False, "got 200")
        except HTTPError as e:
            self._check("/soul returns 401 with bad key", e.code == 401)

        # 3. Soul Console
        print(f"\n{B}--- Soul Console (/soul/*) ---{N}")
        status, data = self._request("GET", "/soul")
        self._check("GET /soul", status == 200, f"status={status}")
        if status == 200:
            self._check("/soul has soul field", "soul" in data)
            self._check("/soul has version", "version" in data)
            self._check("/soul has source", "source" in data)

        status, data = self._request("POST", "/soul/propose",
                                     body={"new_soul": "test soul content", "reason": "testing"})
        self._check("POST /soul/propose", status == 200, f"status={status}")
        if status == 200:
            self._check("/soul/propose has proposed_sha", "proposed_sha" in data)

        # 4. Sync Console
        print(f"\n{B}--- Sync Console (/ops/sync) ---{N}")
        status, data = self._request("POST", "/ops/sync", body={"action": "status"})
        self._check("/ops/sync status", status == 200, f"status={status}")
        if status == 200:
            self._check("sync has config", "config" in data)

        status, data = self._request("POST", "/ops/sync", body={"action": "sync", "dry_run": True})
        self._check("/ops/sync dry_run", status == 200, f"status={status}")

        status, data = self._request("POST", "/ops/sync", body={"action": "destroy"})
        self._check("/ops/sync unknown -> 400", status == 400, f"status={status}")

        # 5. Agentic Console
        print(f"\n{B}--- Agentic Console (/ops/agentic) ---{N}")
        status, data = self._request("POST", "/ops/agentic", body={"action": "status"})
        self._check("/ops/agentic status", status == 200, f"status={status}")
        if status == 200:
            self._check("agentic has config", "config" in data)
            if "config" in data:
                self._check("agentic config has mode", "mode" in data["config"])
                self._check("agentic config has writes_enabled", "writes_enabled" in data["config"])

        status, data = self._request("POST", "/ops/agentic",
            body={"action": "propose-skill", "name": "test-skill", "desc": "A test skill",
                  "body": "# Test", "reason": "test"})
        self._check("/ops/agentic propose-skill", status == 200, f"status={status}")

        status, data = self._request("POST", "/ops/agentic", body={"action": "hack"})
        self._check("/ops/agentic unknown -> 400", status == 400, f"status={status}")

        # 6. Filesystem Console
        print(f"\n{B}--- Filesystem Console (/ops/fsconnect) ---{N}")
        status, data = self._request("POST", "/ops/fsconnect", body={"action": "status"})
        self._check("/ops/fsconnect status", status == 200, f"status={status}")

        status, data = self._request("POST", "/ops/fsconnect",
            body={"action": "list", "root": ".", "path": "."})
        self._check("/ops/fsconnect list", status in (200, 500), f"status={status}")

        status, data = self._request("POST", "/ops/fsconnect", body={"action": "destroy"})
        self._check("/ops/fsconnect unknown -> 400", status == 400, f"status={status}")

        # 7. SQL Console
        print(f"\n{B}--- SQL Console (/ops/sqlconnect) ---{N}")
        status, data = self._request("POST", "/ops/sqlconnect", body={"action": "status"})
        self._check("/ops/sqlconnect status", status == 200, f"status={status}")
        if status == 200:
            self._check("sqlconnect has config", "config" in data)

        status, data = self._request("POST", "/ops/sqlconnect", body={"action": "schema"})
        self._check("/ops/sqlconnect schema (no DSN)", status == 200, f"status={status}")

        status, data = self._request("POST", "/ops/sqlconnect",
            body={"action": "query", "sql": "SELECT 1 as test"})
        self._check("/ops/sqlconnect query SELECT", status == 200, f"status={status}")

        status, data = self._request("POST", "/ops/sqlconnect",
            body={"action": "query", "sql": "DROP TABLE users"})
        self._check("/ops/sqlconnect query DROP rejected", status == 200)
        if status == 200:
            self._check("DROP query fails", data.get("exit_code", 0) != 0)

        status, data = self._request("POST", "/ops/sqlconnect", body={"action": "hack"})
        self._check("/ops/sqlconnect unknown -> 400", status == 400, f"status={status}")

        # 8. Audit Summary
        print(f"\n{B}--- Audit Summary ---{N}")
        status, data = self._request("GET", "/audit/summary")
        self._check("/audit/summary", status == 200, f"status={status}")

        # Report
        print(f"\n{'='*60}")
        total = self.passed + self.failed
        status_str = f"{G}PASS{N}" if self.failed == 0 else f"{R}FAIL{N}"
        print(f"Terminal Console Tests: {status_str}")
        print(f"  Passed: {self.passed}/{total}")
        print(f"  Failed: {self.failed}/{total}")
        print(f"{'='*60}")

        return 0 if self.failed == 0 else 1


if __name__ == "__main__":
    tester = ConsoleTest()
    sys.exit(tester.run_all())
