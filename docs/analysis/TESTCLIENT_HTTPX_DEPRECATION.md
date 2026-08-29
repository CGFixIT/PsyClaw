# Tech Note — `StarletteDeprecationWarning` in the test suite (httpx / TestClient)

**Status:** warning **filtered** (restored 2026-07-27, re-verified 2026-08-29, see below) · no runtime impact · `httpx2` migration still owed before a future Starlette major
**Filed:** 2026-06-19 · **Updated:** 2026-08-29
**Applies to:** `starlette==1.3.1`, `httpx==0.28.1`, `fastapi==0.139.2` (current pins; starlette and httpx are both **direct** pins in all three manifests since 2026-08-02 — starlette also serves `gate.py`/`harness/server.py` middleware imports, httpx serves `llm/client.py`)

**2026-07-27 regression + fix:** the `filterwarnings` entry described below as "Done
2026-07-19" was silently dropped as collateral damage by an unrelated commit
(`0618f04`, wiring the harness entry point into `pyproject.toml`) on 2026-07-22, and a
later same-day fix-up (`f444339`, "restore pyproject.toml so main's CI goes green")
restored three *other* regressions from that same clobbering incident but not this one
— its absence doesn't fail CI, so nothing caught it for five days. Found because the
warning was observed firing live in a routine test run, then confirmed via
`git show <rev>:pyproject.toml` across the three commits plus a repo-wide
`grep filterwarnings` (zero hits outside this doc). Restored verbatim to
`[tool.pytest.ini_options]` in `pyproject.toml`; re-verified silent.

---

## 2026-08-29 re-verification

Everything in this section was checked directly against `origin/main` and the real
published wheels on that date — none of it is recalled from the earlier entries above.

- **The filter is present and verbatim** in `pyproject.toml` `[tool.pytest.ini_options]`,
  now with a backlink comment pointing at this note (added 2026-08-29 — the earlier claim
  that such a comment existed was wrong, which is exactly the failure mode the 2026-07-27
  incident above documents: nothing pointed at the entry, so its deletion was invisible).
- **The warning text still matches the filter byte-for-byte.** Extracted
  `starlette/testclient.py` from the actual `starlette==1.3.1` wheel: the module does
  `try: import httpx2 as httpx` / `except ModuleNotFoundError: import httpx` +
  `warnings.warn("Using \`httpx\` with \`starlette.testclient\` is deprecated; install
  \`httpx2\` instead.", StarletteDeprecationWarning)`. With neither installed it raises
  `RuntimeError` demanding `httpx2` — that is the shape the eventual hard cutover takes.
- **The migration trigger has not fired.** starlette's latest release is `1.6.0` — still
  the 1.x line — and its `testclient.py` carries the *identical* shim and message, so any
  in-1.x starlette bump keeps the filter valid as-is.
- **The message-only filter scoping is still load-bearing.** The conda lane's
  `fastapi=0.115.9` resolves `starlette<0.46.0,>=0.40.0` (checked against fastapi 0.115.9's
  published metadata), and the `starlette==0.45.3` wheel has no
  `StarletteDeprecationWarning` class and no `httpx2` reference at all — a class-qualified
  filter would still fail that lane at pytest startup.
- **Classic `httpx` latest is still `0.28.1`** (only `1.0.dev*` pre-releases beyond it), so
  the runtime pin is current and the "do not fix this by bumping `httpx`" rule below stands.
- **`httpx2` has matured**: latest is `2.12.0`, twelve stable minor releases past the
  "beta/early" state recorded when this note was filed. See Option 3.

---

## Symptom

Every test run that constructs a `TestClient` emits this warning:

```
.../site-packages/fastapi.testclient.py:1: StarletteDeprecationWarning:
  Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa
```

It is raised once per session (at import of `fastapi.testclient`, which re-exports
`starlette.testclient`). It does **not** fail any test, but it is noise on every
`pytest` invocation, including CI. As of 2026-07-19 it is suppressed by an exact
message-text `filterwarnings` entry in `pyproject.toml` (see "Recommendation"),
so it no longer appears in test output; the underlying deprecation is unchanged.

### Where the TestClient is used

The footprint has grown well past the three files this note originally recorded. As of
2026-08-29, **19 test files** import `TestClient` (20 import sites — `tests/test_security.py`
imports it twice, function-locally at lines 170 and 237): every `tests/test_gate*.py` and
`tests/test_harness*.py` file plus `test_security.py`, `test_memory_routes.py`,
`test_runtime_errors.py`, `test_edge_cases.py`, and `test_reasoning_effort.py`
(`grep -rl "import TestClient" tests/` is the authoritative list). Representative sites:
`tests/test_gate.py:18`, `tests/test_gate_ops.py:25`, `tests/test_security.py:170`.

Two more consumers sit **outside pytest**, where the `pyproject.toml` filter does not
apply: `.claude/skills/CyClaw-Sandbox/run_full_verification.py:1222` and its
`.codex/skills/Cyclaw-Sandbox/` twin construct a `TestClient` directly, so the warning
still surfaces in skill verification runs. Cosmetic there too, but worth knowing when
reading their output.

The warning remains purely a **test-time** concern — `httpx` is used at runtime by
`llm/client.py` for Ollama / Grok / Claude calls, but that path does not touch
`starlette.testclient` and is unaffected.

---

## Why it happens

Starlette's `TestClient` is built on top of `httpx`. Starting in the Starlette 1.x line, the
project began migrating its test client onto the **`httpx2`** distribution (the `httpx` 2.x
rewrite, published separately on PyPI as the `httpx2` package). To steer users ahead of a hard
cutover, `starlette.testclient` now emits a `StarletteDeprecationWarning` whenever it detects the
classic `httpx` (1.x / 0.x line) installed instead of `httpx2`.

We currently pin:

```
httpx==0.28.1        # classic httpx, 0.x line; direct pin (serves llm/client.py)
starlette==1.3.1     # direct pin since 2026-08-02; also required by fastapi==0.139.2
```

`httpx==0.28.1` is the classic line, so the warning fires. `httpx2` exists on PyPI
(`2.0.0b1 … 2.4.0` available when this note was filed; `2.12.0` as of 2026-08-29).

---

## Impact assessment

| Horizon | Effect |
|---|---|
| **Now** | None functional. Cosmetic warning on every test session (suppressed under pytest; still visible in the two out-of-pytest skill scripts above). |
| **When Starlette removes the `httpx`-1.x shim** (a future major) | `TestClient` raises `RuntimeError` at import unless `httpx2` is present (the 1.x shim's own no-httpx branch already does exactly that). CI test collection breaks across the 19 consuming test files. |
| **A Starlette major generally** | Not only a test concern: `gate.py` (5 sites) and `harness/server.py` (4 sites) import starlette middleware/request/response classes directly, so a 2.x bump lands on first-party runtime code too. That is a separate, larger review than the TestClient item tracked here — noted so the "purely test-time" framing above isn't read as covering a major bump. |

This is a "fix before the next Starlette major" item, not an emergency. It is tracked here so the
warning is not silently ignored until it becomes a hard break.

---

## Options (do **not** apply blindly — see recommendation)

1. **Do nothing yet (current state).** Acceptable while the shim exists. Risk: forgetting until a
   Starlette bump turns the warning into an `ImportError`.

2. **Silence the warning only — APPLIED 2026-07-19.** `pyproject.toml`
   `[tool.pytest.ini_options]` now carries a filter scoped by the exact message text:

   ```toml
   filterwarnings = [
       'ignore:Using `httpx` with `starlette\.testclient` is deprecated; install `httpx2` instead\.',
   ]
   ```

   Pros: zero dependency churn. Cons: hides the signal; the underlying break still lands later.
   The filter is deliberately scoped by full message text (unique to this warning) rather than the
   broad `:DeprecationWarning` category originally sketched here. It is **not** class-qualified
   (`:starlette.exceptions.StarletteDeprecationWarning`) on purpose: the conda lane
   (`python-package-conda.yml`) installs `fastapi=0.115.9` → starlette 0.4x, where that class does
   not exist, and pytest resolves filter categories at startup — a class-qualified filter fails the
   whole lane with `AttributeError` before any test runs (observed 2026-07-19). Message-only parses
   in both the pip lane (starlette 1.3.1) and the conda lane.

3. **Migrate the test client to `httpx2`.** Add `httpx2` to the test/dev requirements so
   `starlette.testclient` picks it up. This is the direction Starlette is steering toward.
   Caveats, updated 2026-08-29:
   - `httpx2` is a **major rewrite**; its request/response API differs from classic `httpx`.
     The two are **not** drop-in interchangeable. It is no longer beta, though — `2.12.0`
     is current, twelve stable minors on from the `2.0.0b1` this note originally cited.
   - The coexistence question this note used to leave open is **answered by starlette's own
     source** (verified in the 1.3.1 wheel): `starlette.testclient` does
     `try: import httpx2 as httpx` / `except ModuleNotFoundError: import httpx`. The two are
     different top-level modules from different distributions, so installing `httpx2` flips
     the test client over automatically while `llm/client.py`'s `import httpx` keeps
     resolving to classic `httpx==0.28.1` untouched. No resolver verification needed — the
     real cost is the next bullet.
   - Any direct `httpx`-typed assertions in the **19** TestClient-consuming test files
     (status codes, JSON bodies, response attributes) must be re-checked against the
     `httpx2` response surface. That revalidation, not dependency risk, is now the bulk of
     the migration.

4. **Drop `TestClient` entirely** in favour of an ASGI transport driven directly through `httpx`
   (`httpx.ASGITransport` + `httpx.AsyncClient`). Removes the Starlette-testclient dependency and
   the warning, at the cost of rewriting the two test files to async. Larger diff; only worth it if
   the suite is moving async anyway.

---

## Recommendation

Short term: **Option 2** (filter the warning) to keep CI logs clean and intentional, paired with a
tracking reference to this note so the deprecation is not lost. **Done 2026-07-19** — the filter
lives in `pyproject.toml`; since 2026-08-29 it carries a comment pointing back to this note
(the claim that one existed earlier was wrong — see the 2026-08-29 re-verification section).

Before the next Starlette **major** bump: **Option 3** — add `httpx2` for the test client and
re-validate the 19 TestClient-consuming test files (see "Where the TestClient is used"). Two
things about spotting that trigger, verified 2026-08-29:

- `starlette==1.3.1` is a direct pin dependabot tracks (no `ignore` entry for it — only numpy
  is ignored), so a 2.x bump **will** get a PR. But `.github/dependabot.yml` groups the whole
  pip ecosystem (`pip-all`, `patterns: ["*"]`, `open-pull-requests-limit: 4`), so the bump
  arrives **buried inside a grouped multi-dependency PR**, not as a standalone
  "starlette 1.x → 2.x" title. Read grouped dependabot diffs for the starlette line; don't
  wait for a PR named after it.
- Do not migrate speculatively. The original reason ("httpx2 is still beta") no longer holds
  — `httpx2` is stable at 2.12.0 — but the calculus is unchanged: the shim still works, the
  1.x line still ships it (confirmed through 1.6.0), and the migration's real cost is
  re-validating 19 test files with no forcing event yet.

Do **not** "fix" this by bumping the runtime `httpx==0.28.1` pin — that pin serves `llm/client.py`,
not the test client, and changing it has nothing to do with the warning.

---

## Reproduction

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt -c constraints.txt
# The pyproject.toml filter suppresses the warning by default; override it to reproduce
# (pip path only — the class-qualified -W below needs starlette 1.x; the conda lane's
# starlette 0.4x has no such class, and the warning does not fire there anyway):
GROK_API_KEY=dummy pytest tests/test_gate.py -q -W "always::starlette.exceptions.StarletteDeprecationWarning" 2>&1 | grep -i deprecat
# -> StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
```
