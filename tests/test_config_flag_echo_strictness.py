"""Every memory-flag echo must agree with the gate it reports on.

PR #1199 renamed `memory.facts.enabled` to `facts.retrieval_enabled` and put the
resolution behind `memory/flags.facts_retrieval_enabled`, whose module docstring
states the reason: *"Three readers of one flag drift; one function does not."*
PR #1207 then made the harness console's `facts` echo strict to match, and
disclosed in its own body that the sibling keys were left loose.

This pins the whole set, in both echoes, because the sibling gaps were the larger
problem:

* `memory/mirror.py.status_dict` is the body of `GET /memory/status`. It used the
  shared helper for `facts` and `is True` for the master key, then hand-rolled
  loose `bool()` for the other five -- including `propose_apply` and
  `export_html`, the write and export gates.
* `harness/memory_notes.rag_flags` had the master `enabled` loose, which is the
  worst of them: six independent gates read that key strictly
  (`memory/store.py`, `memory/retrieval_adapter.py`, `retrieval/hybrid_search.py`,
  `graph.py`, `gate_memory.py`, `memory/mirror.py`).

Why a quoted value is the realistic trigger rather than a contrived one: the
`memory:` block has **no validator**. There is no `validate_memory_config`, and
gate.py's validators do not cover it, so `enabled: "false"` boots silently. Under
`bool()` every non-empty string is truthy, so the console reported a capability
ON while the gate refused it -- and `"true"`, the value an operator writes when
they mean to switch something on, fails the same way.

These tests assert AGREEMENT with the gate rather than a fixed expected value.
The contract is the mirror, so the mirror is what must hold.
"""

from __future__ import annotations

import pytest

from harness.memory_notes import rag_flags
from memory.mirror import status_dict

# Ten shapes spanning the ways a config value arrives: YAML-quoted booleans (the
# real-world trigger), truthy/falsy non-booleans, and the two real booleans.
# Only the literal True may read as enabled.
_SHAPES = ["false", "true", "no", "yes", "", 0, 1, None, False, True]

# key in the memory: block -> key in each echo's output dict
_MIRROR_KEYS = {
    "episodes": "episodes_enabled",
    "retrieval_fusion": "retrieval_fusion_enabled",
    "propose_apply": "propose_apply_enabled",
    "export_html": "export_html_enabled",
    "consolidation": "consolidation_enabled",
}


def _cfg(block: dict) -> dict:
    return {"memory": block}


@pytest.mark.parametrize("value", _SHAPES)
@pytest.mark.parametrize("section", sorted(_MIRROR_KEYS))
def test_mirror_status_echo_agrees_with_the_gate(section: str, value: object):
    """GET /memory/status must not claim a capability its gate refuses."""
    block = {"enabled": True, "db_path": ":memory:", section: {"enabled": value}}
    out = status_dict(_cfg(block))
    assert out[_MIRROR_KEYS[section]] is (value is True), (
        f"/memory/status reported {section}={out[_MIRROR_KEYS[section]]!r} "
        f"for a configured value of {value!r}; every gate on this key accepts "
        f"only the literal True"
    )


@pytest.mark.parametrize("value", _SHAPES)
def test_mirror_master_enabled_echo_agrees_with_the_gate(value: object):
    out = status_dict(_cfg({"enabled": value, "db_path": ":memory:"}))
    assert out["enabled"] is (value is True)


@pytest.mark.parametrize("value", _SHAPES)
@pytest.mark.parametrize("section", ["episodes", "retrieval_fusion"])
def test_harness_rag_flags_siblings_agree_with_the_gate(section: str, value: object):
    """The console echo #1207 fixed for `facts` but left loose for the siblings."""
    flags = rag_flags({"memory": {"enabled": True, section: {"enabled": value}}})
    assert flags[section] is (value is True)


@pytest.mark.parametrize("value", _SHAPES)
def test_harness_rag_flags_master_enabled_agrees_with_the_gate(value: object):
    """Six gates read this key strictly; this echo was the last loose reader."""
    flags = rag_flags({"memory": {"enabled": value}})
    assert flags["enabled"] is (value is True)
