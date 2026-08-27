"""Unit tests for memory/selftest.py and memory/consolidation.py.

Both modules were shipped at 0% coverage. memory.selftest.main() is the
`python -m memory.selftest` pre-flight — a self-contained end-to-end pass over
the whole memory subsystem (SQLite schema, propose/apply governance, FTS5
search, episode staging, injection/reason gates, retrieval fusion, HTML export)
using only a tempdir; no network, no embedding model. This runs it directly and
asserts it returns 0, so a regression anywhere in that chain fails here instead
of only in the ad-hoc CLI.

memory.consolidation.run_consolidation is a v1 stub whose contract is that it
NEVER consolidates — even if an operator flips the config flag on. That safety
property is asserted here for both flag states.
"""

from __future__ import annotations

import memory.selftest as selftest
from memory.consolidation import run_consolidation


class TestMemorySelftest:
    def test_main_returns_zero(self, capsys):
        rc = selftest.main()
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "ALL PASS" in out
        assert "FAIL" not in out

    def test_every_named_check_reports_pass(self, capsys):
        # The selftest prints one "PASS <name>" line per check. Lock the full
        # set so a silently-dropped check (fewer lines, still rc 0) is caught.
        selftest.main()
        out = capsys.readouterr().out
        expected = {
            "schema_create", "propose", "apply", "fts_hit", "episode_stage",
            "reason_gate", "injection_refuse", "fusion", "export_html",
            "consolidation_stub", "fusion_disabled_noop",
        }
        reported = {line.split("PASS", 1)[1].strip()
                    for line in out.splitlines() if "PASS" in line}
        assert expected <= reported, f"missing checks: {expected - reported}"


class TestRunConsolidation:
    def test_disabled_when_flag_absent(self):
        result = run_consolidation({})
        assert result == {"status": "disabled",
                           "reason": "consolidation not implemented"}

    def test_disabled_when_flag_false(self):
        cfg = {"memory": {"consolidation": {"enabled": False}}}
        assert run_consolidation(cfg)["status"] == "disabled"

    def test_still_disabled_even_when_flag_true(self):
        # v1 safety contract: flipping the flag must NOT start consolidating.
        cfg = {"memory": {"consolidation": {"enabled": True}}}
        result = run_consolidation(cfg)
        assert result == {"status": "disabled",
                          "reason": "consolidation not implemented"}
