#!/usr/bin/env python3
"""harness.html API emulation — exercises the exact HTTP fetch lifecycle that
static/harness.html performs in the browser, using httpx from the venv.

Mirrors terminal_emulation.py's approach for the harness console (port 8790
by default) instead of the RAG gateway (port 8787). The harness's five
state-changing POSTs, GET /api/github/status and the three /api/agent/* run
routes are gated on a Bearer
CYCLAW_API_KEY (utils/auth.py) AND a per-process CSRF token minted at server
start and embedded only in the page GET / serves, so this script reads the
key from the environment, fetches GET / once to extract the token the same
way harness.html's own JS does, and sends both on every request; the open
read routes ignore both. Loopback bind plus TrustedHostMiddleware plus an
Origin/Sec-Fetch-Site check remain the rest of the boundary, per
harness/server.py's own docstring.

Verifies, in the same order harness.html's own on-load + first-use calls fire:
  1. GET  /api/status     (header pills: model, tokens, provider)
  2. GET  /api/registry   (sidebar: skills/tools/connectors panes)
  3. GET  /api/sessions   (sidebar: sessions pane, empty on a fresh home)
  4. POST /api/sessions   (/session new)
  5. GET  /api/sessions/{id}          (/session use <id>)
  6. POST /api/sessions/{id}/rename   (/session rename)
  7. GET  /api/sessions/{bogus-id}    (expect 404 -- SessionStoreError path)
  8. GET+POST /api/soul   (soul toggle round-trip)
  9. POST /api/model      (/model use <name>)
 10. POST /api/chat       (message send -- accepts a real reply OR the
     documented 502 HarnessLLMError when no chat backend answers; pair this
     script with mock_ollama.py on :11434 for a deterministic 200 instead)
 11. GET  /api/github/status  (subprocess-backed; accepts any well-formed
     JSON envelope -- a sandbox may lack a configured git remote)
 12. GET  /api/harness/runs  (/harness command)
 13. GET  /api/agent/checks  (/agent checks) + auth-gate on the write routes
 14. POST /api/sessions/{id}/goal  (/goal set, persist; listing omits goal)
 15. POST /api/chat {loop: true}   (/loop with goal = chat-only 200/502;
     clear goal; then 400 LOOP_REQUIRES_GOAL)
 16. POST /api/chat/cancel   (/loop stop -- idempotent when nothing is running)

Usage (called from verify.sh while the harness server is running):
    python harness_emulation.py <base_url>  (default: loopback:8790)
"""

import os
import re
import sys

_CSRF_META_RE = re.compile(r'<meta name="csrf-token" content="([^"]*)">')


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790"  # DevSkim: ignore DS162092,DS137138 — loopback-only by design (harness.host in harness/config.py)

    try:
        import httpx
    except ImportError:
        print("httpx not installed; skipping harness emulation (install with pip install httpx)")
        return 0

    failures = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {label}" + (f"  [{detail}]" if detail else ""))
        if not ok:
            failures += 1

    print(f"=== harness.html API emulation → {base} ===")
    print()

    # The five state-changing POSTs, GET /api/github/status and the three
    # /api/agent/* run routes require a Bearer CYCLAW_API_KEY (utils/auth.py)
    # AND the per-process CSRF token embedded in the page GET / serves. Sent
    # on every request here: the open read routes ignore both, and a per-path
    # branch would drift from the server's guard list. An unset key means
    # those routes correctly 401 and the emulation fails loudly rather than
    # silently skipping them.
    api_key = os.environ.get("CYCLAW_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(base_url=base, timeout=10.0) as probe:
        page = probe.get("/").text
    match = _CSRF_META_RE.search(page)
    if match and match.group(1) and match.group(1) != "__CYCLAW_CSRF_TOKEN__":
        headers["X-CyClaw-CSRF"] = match.group(1)

    with httpx.Client(base_url=base, timeout=10.0, headers=headers) as client:

        # ── 1. GET /api/status (header pills) ─────────────────────────────
        print("[1] GET /api/status  (harness.html header pills)")
        try:
            r = client.get("/api/status")
            d = r.json()
            print(f"       model        : {d.get('model')}")
            print(f"       provider     : {d.get('provider')}")
            print(f"       soul_enabled : {d.get('soul_enabled')}")
            print(f"       home         : {d.get('home')}")
            for field in ("version", "model", "provider", "base_url", "soul_enabled",
                          "home", "repo_root", "sessions", "total_tokens", "layout"):
                check(f"/api/status has '{field}'", field in d)
        except Exception as exc:
            check("/api/status", False, repr(exc))
        print()

        # ── 2. GET /api/registry (sidebar registry pane) ──────────────────
        print("[2] GET /api/registry  (harness.html registry pane)")
        try:
            r = client.get("/api/registry")
            d = r.json()
            print(f"       skills       : {len(d.get('skills', []))}")
            print(f"       tools        : {len(d.get('tools', []))}")
            print(f"       connectors   : {len(d.get('connectors', []))}")
            check("/api/registry has skills/tools/connectors",
                  all(k in d and isinstance(d[k], list) for k in ("skills", "tools", "connectors")))
        except Exception as exc:
            check("/api/registry", False, repr(exc))
        print()

        # ── 2b. GET /api/tools (/tools wiring diagram) ────────────────────
        print("[2b] GET /api/tools  (/tools slash command)")
        try:
            r = client.get("/api/tools")
            d = r.json()
            names = {t.get("name") for t in d.get("tools") or []}
            check(
                "/api/tools returns a wiring diagram",
                r.status_code == 200
                and isinstance(d.get("diagram"), str)
                and "HARNESS TOOLS" in d.get("diagram", ""),
                f"status={r.status_code}",
            )
            check(
                "/api/tools lists goal, loop, and hybrid_search",
                {"goal", "loop", "hybrid_search"} <= names,
                f"names={sorted(names)}",
            )
            check(
                "/api/tools harness rows are wired",
                all(t.get("wired") for t in (d.get("tools") or []) if t.get("kind") == "harness"),
            )
        except Exception as exc:
            check("GET /api/tools", False, repr(exc))
        print()

        # ── 2c. GET /api/skills (/skills wiring diagram) ──────────────────
        print("[2c] GET /api/skills  (/skills slash command)")
        try:
            r = client.get("/api/skills")
            d = r.json()
            names = {s.get("name") for s in d.get("skills") or []}
            check(
                "/api/skills returns a wiring diagram",
                r.status_code == 200
                and isinstance(d.get("diagram"), str)
                and "HARNESS SKILLS" in d.get("diagram", ""),
                f"status={r.status_code}",
            )
            check(
                "/api/skills lists ponytail and invariant-guard",
                {"ponytail", "invariant-guard"} <= names,
                f"names={sorted(names)}",
            )
            check(
                "/api/skills prompt/check rows are wired",
                all(
                    s.get("wired")
                    for s in (d.get("skills") or [])
                    if s.get("role") in {"prompt", "check"}
                ),
            )
        except Exception as exc:
            check("GET /api/skills", False, repr(exc))
        print()

        # ── 2d. GET /api/web (/web allowlist status) ─────────────────────
        print("[2d] GET /api/web  (/web slash command)")
        try:
            r = client.get("/api/web")
            d = r.json()
            check(
                "/api/web defaults to off with an empty allowlist",
                r.status_code == 200
                and d.get("enabled") is False
                and d.get("allowlist") == [],
                f"status={r.status_code} enabled={d.get('enabled')}",
            )
            denied = client.post("/api/web/fetch", json={"url": "https://example.com/"})
            code = (denied.json().get("detail") or {}).get("code")
            check(
                "/api/web/fetch is 409 WEB_DISABLED when off",
                denied.status_code == 409 and code == "WEB_DISABLED",
                f"status={denied.status_code} code={code}",
            )
        except Exception as exc:
            check("GET /api/web", False, repr(exc))
        print()

        # ── 2e. GET /api/memory (/memory toggle, default off) ────────────
        print("[2e] GET /api/memory  (/memory slash command)")
        try:
            r = client.get("/api/memory")
            d = r.json()
            check(
                "/api/memory defaults to off with zero notes",
                r.status_code == 200
                and d.get("enabled") is False
                and d.get("count") == 0
                and d.get("rag", {}).get("writable_from_harness") is False,
                f"status={r.status_code} enabled={d.get('enabled')} count={d.get('count')}",
            )
            added = client.post("/api/memory/add", json={"text": "prefer ruff"})
            check(
                "/api/memory/add pins one note",
                added.status_code == 200 and added.json().get("count") == 1,
                f"status={added.status_code}",
            )
            on = client.post("/api/memory", json={"enabled": True})
            check("/api/memory on flips enabled", on.json().get("enabled") is True)
            client.post("/api/memory", json={"enabled": False})
            client.post("/api/memory/clear")
        except Exception as exc:
            check("GET /api/memory", False, repr(exc))
        print()

        # ── 3. GET /api/sessions (sidebar sessions pane) ──────────────────
        print("[3] GET /api/sessions  (harness.html sessions pane, pre-create)")
        try:
            r = client.get("/api/sessions")
            d = r.json()
            check("/api/sessions has 'sessions' list", isinstance(d.get("sessions"), list))
        except Exception as exc:
            check("/api/sessions", False, repr(exc))
        print()

        # ── 4-7. Session lifecycle (/session new|use|rename) ──────────────
        print("[4] POST /api/sessions  (/session new)")
        session_id = None
        try:
            r = client.post("/api/sessions", json={"title": "harness emulation smoke"})
            check("/api/sessions create -> 201", r.status_code == 201, f"status={r.status_code}")
            d = r.json()
            session_id = d.get("session_id")
            check("created session has session_id", bool(session_id))
        except Exception as exc:
            check("POST /api/sessions", False, repr(exc))
        print()

        print(f"[5] GET /api/sessions/{{id}}  (/session use {session_id})")
        if session_id:
            try:
                r = client.get(f"/api/sessions/{session_id}")
                d = r.json()
                check("/api/sessions/{id} echoes session_id", d.get("session_id") == session_id)
                check("/api/sessions/{id} has 'messages'", "messages" in d)
            except Exception as exc:
                check("GET /api/sessions/{id}", False, repr(exc))
        else:
            check("GET /api/sessions/{id}", False, "no session_id from step 4")
        print()

        print("[6] POST /api/sessions/{id}/rename  (/session rename)")
        if session_id:
            try:
                r = client.post(f"/api/sessions/{session_id}/rename", json={"title": "renamed by emulation"})
                d = r.json()
                check("rename applied", d.get("title") == "renamed by emulation", f"title={d.get('title')!r}")
            except Exception as exc:
                check("POST /api/sessions/{id}/rename", False, repr(exc))
        else:
            check("POST /api/sessions/{id}/rename", False, "no session_id from step 4")
        print()

        print("[7] GET /api/sessions/{bogus}  (unknown id -> 404)")
        try:
            r = client.get("/api/sessions/000000000000")
            check("unknown session -> HTTP 404", r.status_code == 404, f"status={r.status_code}")
        except Exception as exc:
            check("GET /api/sessions/{bogus}", False, repr(exc))
        print()

        # ── 8. Soul toggle round-trip (/soul on|off|status) ───────────────
        print("[8] GET+POST /api/soul  (/soul toggle — harness-local, soul.md untouched)")
        try:
            before = client.get("/api/soul").json().get("enabled")
            flipped = client.post("/api/soul", json={"enabled": not before}).json().get("enabled")
            check("/api/soul toggle flips 'enabled'", flipped == (not before),
                  f"before={before}, after={flipped}")
            restored = client.post("/api/soul", json={"enabled": before}).json().get("enabled")
            check("/api/soul restored to original value", restored == before)
        except Exception as exc:
            check("/api/soul toggle", False, repr(exc))
        print()

        # ── 9. Model selection (/model use <name>) ────────────────────────
        print("[9] POST /api/model  (/model use)")
        try:
            r = client.post("/api/model", json={"model": "qwen3.6:27b"})
            d = r.json()
            check("/api/model select echoes model", d.get("model") == "qwen3.6:27b", f"model={d.get('model')!r}")
        except Exception as exc:
            check("POST /api/model", False, repr(exc))
        print()

        # ── 10. Chat send ──────────────────────────────────────────────────
        print("[10] POST /api/chat  (message send)")
        try:
            r = client.post("/api/chat", json={"message": "hello from harness_emulation.py"})
            if r.status_code == 200:
                d = r.json()
                check("/api/chat 200: has session_id/reply/model/usage/tally",
                      all(k in d for k in ("session_id", "reply", "model", "usage", "tally")))
            elif r.status_code == 502:
                # Documented, expected outcome with no live chat backend
                # (HarnessLLMError -> _HTTP_BAD_GATEWAY). Run mock_ollama.py on
                # :11434 first (matching config.yaml's default base_url) for a
                # deterministic 200 instead of this fallback branch.
                check("/api/chat 502: well-formed error envelope",
                      isinstance(r.json().get("detail"), dict), "no live chat backend — expected without mock_ollama.py")
            else:
                check("/api/chat", False, f"unexpected status={r.status_code}")
        except Exception as exc:
            check("POST /api/chat", False, repr(exc))
        print()

        # ── 11. GitHub status (/github) ────────────────────────────────────
        print("[11] GET /api/github/status  (/github — subprocess-backed, read-only)")
        try:
            r = client.get("/api/github/status")
            # Accept either a successful ops envelope or the OpsError 400 path
            # (e.g. no git remote configured in this sandbox) -- both are
            # well-formed JSON, matching this endpoint's documented contract.
            check("/api/github/status returns well-formed JSON", isinstance(r.json(), dict),
                  f"status={r.status_code}")
        except Exception as exc:
            check("GET /api/github/status", False, repr(exc))
        print()

        # ── 12. Harness optimizer runs (/harness) ─────────────────────────
        print("[12] GET /api/harness/runs  (/harness)")
        try:
            r = client.get("/api/harness/runs")
            d = r.json()
            check("/api/harness/runs has 'runs' + 'count'", "runs" in d and "count" in d)
        except Exception as exc:
            check("GET /api/harness/runs", False, repr(exc))
        print()

        # ── 13. GET /api/agent/checks (/agent checks) ─────────────────────
        # The only one of the four agent routes that is safe to exercise here.
        # The other three drive a real `python -m agentic.cli` subprocess: a
        # run clones a repository, calls a model and can block for 900s, and a
        # decision reaches a git write. A smoke test must not do either, so
        # this asserts the console's discovery call plus the gate on the rest.
        print("[13] GET /api/agent/checks  (/agent checks)")
        try:
            r = client.get("/api/agent/checks")
            profiles = r.json().get("profiles")
            check("/api/agent/checks lists named profiles", bool(profiles))
            check(
                "every profile carries a name + description",
                all(p.get("name") and p.get("description") for p in profiles or []),
            )
        except Exception as exc:
            check("GET /api/agent/checks", False, repr(exc))

        for path in ("/api/agent/run", f"/api/agent/runs/{'0' * 32}/decision"):
            try:
                unauthed = client.post(path, json={}, headers={"Authorization": "Bearer wrong"})
                check(f"POST {path} rejects a bad key", unauthed.status_code == 401,
                      f"HTTP {unauthed.status_code}")
            except Exception as exc:
                check(f"POST {path} auth gate", False, repr(exc))
        print()

        # ── 14. Session goal (/goal) ──────────────────────────────────────
        print("[14] POST /api/sessions/{id}/goal  (/goal set|clear)")
        if session_id:
            try:
                r = client.post(
                    f"/api/sessions/{session_id}/goal",
                    json={"goal": "  emulation goal  "},
                )
                check(
                    "/api/sessions/{id}/goal set trims and echoes",
                    r.status_code == 200 and r.json().get("goal") == "emulation goal",
                    f"status={r.status_code} goal={r.json().get('goal')!r}",
                )
                fetched = client.get(f"/api/sessions/{session_id}").json()
                check(
                    "GET /api/sessions/{id} persists goal",
                    fetched.get("goal") == "emulation goal",
                )
                listed = client.get("/api/sessions").json().get("sessions") or []
                match = next((s for s in listed if s.get("session_id") == session_id), {})
                check(
                    "GET /api/sessions listing omits goal",
                    "goal" not in match,
                )
                looped = client.post(
                    "/api/chat",
                    json={
                        "message": "emulation loop turn",
                        "session_id": session_id,
                        "loop": True,
                    },
                )
                check(
                    "/loop with goal is chat-only (200 or documented 502)",
                    looped.status_code in (200, 502),
                    f"status={looped.status_code}",
                )
                cleared = client.post(
                    f"/api/sessions/{session_id}/goal",
                    json={"goal": ""},
                )
                check(
                    "/api/sessions/{id}/goal empty string clears",
                    cleared.status_code == 200 and cleared.json().get("goal") == "",
                )
                denied = client.post(
                    "/api/chat",
                    json={
                        "message": "emulation loop without goal",
                        "session_id": session_id,
                        "loop": True,
                    },
                )
                denied_body = denied.json() if denied.headers.get("content-type", "").startswith("application/json") else {}
                denied_detail = denied_body.get("detail") if isinstance(denied_body, dict) else {}
                denied_code = denied_detail.get("code") if isinstance(denied_detail, dict) else None
                check(
                    "/loop without goal is 400 LOOP_REQUIRES_GOAL",
                    denied.status_code == 400 and denied_code == "LOOP_REQUIRES_GOAL",
                    f"status={denied.status_code} code={denied_code!r}",
                )
            except Exception as exc:
                check("POST /api/sessions/{id}/goal", False, repr(exc))
        else:
            check("POST /api/sessions/{id}/goal", False, "no session_id from step 4")
        print()

        # ── 15. /loop (chat-only; never /api/agent/*) ─────────────────────
        # Step 14 already fired loop-with-goal then LOOP_REQUIRES_GOAL after
        # clear. This banner exists so the verify.sh log names the command.
        print("[15] POST /api/chat {loop:true}  (/loop — asserted in step 14)")
        print()

        # ── 16. Chat cancel (/loop stop) ──────────────────────────────────
        print("[16] POST /api/chat/cancel  (/loop stop -- idempotent)")
        try:
            r = client.post("/api/chat/cancel")
            check(
                "/api/chat/cancel is idempotent",
                r.status_code == 200 and r.json().get("cancelled") is True,
                f"status={r.status_code}",
            )
        except Exception as exc:
            check("POST /api/chat/cancel", False, repr(exc))
        print()

    print()
    if failures:
        print(f"harness.html emulation FAILED ({failures} check(s))")
        return 1
    print("harness.html emulation PASSED — all endpoint flows matched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
