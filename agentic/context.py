"""Higher-level read-only GitHub *context* fetchers for human/agent consumption.

Thin, audit-friendly wrappers over ``agentic.gh_client.run_read`` that assemble
structured context bundles (a PR plus its diff, an issue, a repo overview). These
are pure reads -- nothing here mutates GitHub state, and the module is never
imported by the request path.

Each underlying ``run_read`` call already emits its own audit event, so these
wrappers do not re-log the read itself; they shape the returned data and add one
further event per injection finding (see ``_injection_findings``).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from agentic.config import AgenticConfig
from agentic.gh_client import DEFAULT_MIN_GH, run_read
from guardrails.rails import (
    build_injection_pattern_sources,
    compile_injection_patterns,
    scan_injection_patterns,
)
from utils.errors import AgenticError
from utils.logger import _get_config, audit_log

_DEFAULT_LIST_LIMIT = 10

# Codes for the findings attached to every context bundle. Stable strings: a
# downstream consumer keys on them, and metrics.py aggregates by event+code.
INJECTION_FINDING_CODE = "github_content_injection_pattern"
SCANNER_UNAVAILABLE_CODE = "github_content_scanner_unavailable"

# Unicode general categories stripped before the second scan pass: Cf (format --
# zero-width space/joiner, soft hyphen, BOM, bidi overrides) and Mn (non-spacing
# combining marks). Both are invisible or near-invisible in a rendered PR body
# and neither changes how a model reads the text, so both are free ways to break
# a literal pattern like "ignore previous instructions".
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Mn"})

# Homoglyphs that render as ASCII in the fonts GitHub uses. Deliberately a short
# hand-audited list rather than a full confusables table: an over-broad mapping
# would fold legitimate non-English text into false positives on a path that
# also feeds `agentic.cli context`, which a human reads.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "х": "x", "у": "y", "і": "i", "ј": "j", "һ": "h",
    "ο": "o", "α": "a", "ρ": "p", "Α": "A", "Ο": "O",
})


def _normalize_for_scan(text: str) -> str:
    """Fold away the cheapest ways to defeat a literal injection pattern.

    NFKD first, not NFKC: decomposition splits a precomposed accent into base +
    combining mark so the strip below can remove the mark, where composition
    would leave "ignóre" as a single Ll codepoint that no strip can reach.
    Compatibility decomposition also collapses fullwidth and other lookalike
    forms to ASCII. Then drop invisible formatting/combining characters, then
    map a small set of audited homoglyphs. Advisory only, and never a substitute
    for the raw scan: this exists so a one-codepoint edit does not silently
    pass, not because it makes the denylist complete. It cannot.
    """
    folded = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in folded if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES)
    return stripped.translate(_CONFUSABLES)


def _injection_findings(
    app_cfg: dict | None,
    repo: str,
    number: int | None,
    fields: Sequence[tuple[str, object]],
) -> list[dict]:
    """Scan GitHub-sourced text for injection shapes. Advisory: never raises, never blocks.

    Everything ``gh`` returns is third-party text -- a PR body, an issue comment, a
    diff -- authored by anyone who can open one. Until now it crossed into CyClaw
    unscanned, because the only injection scanners in this package run over
    *locally authored* text (``agentic.registry``, ``agentic.harness_optimizer
    .governance``). That is fine while a human reads the JSON, and stops being fine
    the moment a planner consumes it.

    Findings are metadata, not a gate. Severity is always ``"warning"``, never the
    ``"critical"`` string the harness optimizer's acceptance gate hard-rejects on:
    this is text CyClaw is *reading*, so a PR that merely discusses prompt
    injection must still be fetchable. The layer that feeds a bundle to a model is
    the one that decides to refuse -- and after this it cannot claim it was not
    told.

    The pattern union is the shared ``guardrails.rails`` one, so this scanner
    cannot drift from the registry, fsconnect, and harness-optimizer scanners.
    ``app_cfg`` defaults to the on-disk config (``lru_cache``-backed) rather than
    ``{}`` so an omitted argument does not silently narrow the set to the OWASP
    baseline and drop every operator-configured ``banned_patterns`` entry.
    """
    cfg = app_cfg if app_cfg is not None else _get_config()
    compiled = compile_injection_patterns(tuple(build_injection_pattern_sources(cfg)))

    findings: list[dict] = []
    if not compiled:
        # The registry refuses to construct on an empty pattern set because its
        # scan is an enforced write gate. This one is a read path, and refusing to
        # show a PR because an operator's regex has a typo is worse than showing
        # it. So reads stay available -- but an empty set makes the scan a silent
        # no-op, which is precisely the failure that would go unnoticed, so the
        # bundle says so out loud instead.
        findings.append({"severity": "warning", "code": SCANNER_UNAVAILABLE_CODE, "field": None, "patterns": []})

    for field, value in fields:
        if not isinstance(value, str) or not value:
            continue
        # Scan the raw text AND a normalized copy. scan_injection_patterns
        # applies only re.IGNORECASE to the bytes it is given, so a single
        # zero-width space, soft hyphen, or Cyrillic homoglyph inside a phrase
        # defeats every pattern while reading identically to a human on
        # github.com and identically to an LLM. Normalizing HERE rather than in
        # guardrails/rails.py keeps the shared primitive -- also used by the
        # registry write gate, fsconnect, and the harness optimizer -- byte
        # identical; widening a shared sanitizer is a High-tier change and this
        # is a read-path finding producer. Both are scanned because
        # normalization can only ever be approximate: a hit on either counts.
        # list, not tuple: `patterns` is part of the bundle's JSON shape and of
        # the audit event, and dict.fromkeys keeps first-seen order so a phrase
        # matching in both passes is reported once.
        matched = list(dict.fromkeys(
            scan_injection_patterns(value, compiled) + scan_injection_patterns(_normalize_for_scan(value), compiled)
        ))
        if matched:
            # scan_injection_patterns returns the pattern SOURCES, not match
            # objects, so a finding names which rule fired without echoing the
            # attacker-controlled text that fired it.
            findings.append(
                {"severity": "warning", "code": INJECTION_FINDING_CODE, "field": field, "patterns": matched}
            )

    for finding in findings:
        audit_log(
            {
                "event": "agentic_context_injection_finding",
                "repo": repo,
                "number": number,
                "field": finding["field"],
                "code": finding["code"],
                "patterns": finding["patterns"],
            },
            cfg=cfg,
        )
    return findings


def _text_fields(obj: object, prefix: str, keys: Sequence[str]) -> list[tuple[str, object]]:
    """Pull ``keys`` off a gh JSON object as ``(dotted_field_name, value)`` pairs.

    Tolerant by design: ``gh`` returns ``null`` for an absent body and this must
    never turn a successful read into an exception.
    """
    if not isinstance(obj, dict):
        return []
    return [(f"{prefix}.{key}", obj.get(key)) for key in keys]


def _shortlist_title_fields(shortlist: dict, prefix: str) -> list[tuple[str, object]]:
    """Pull the titles out of a ``_list_with_more`` shortlist for scanning.

    Only titles: the list ``--json`` field sets carry no bodies (see ``_PR_LIST_FIELDS``
    in gh_client), and a title is attacker-controlled the same as a body is.
    """
    fields: list[tuple[str, object]] = []
    for i, item in enumerate(shortlist.get("items") or []):
        fields.extend(_text_fields(item, f"{prefix}[{i}]", ("title",)))
    return fields


def _guard_read_op(cfg: AgenticConfig, op: str) -> None:
    """Reject ops the operator has not allow-listed in config.allowed_read_ops."""
    if op not in cfg.allowed_read_ops:
        raise AgenticError(
            f"read op {op!r} is not in agentic.allowed_read_ops",
            details={"op": op, "allowed": list(cfg.allowed_read_ops)},
        )


def _read(cfg: AgenticConfig, op: str, **kwargs: object) -> dict:
    """``run_read`` with the repo, version floor, and resilience knobs from cfg.

    Centralises threading ``gh_timeout_sec`` / ``gh_retries`` (and ``gh_min_tuple``)
    into every read so the fetchers stay declarative and a config change applies
    uniformly. ``getattr`` defaults keep this safe for an older AgenticConfig built
    without the resilience fields.
    """
    return run_read(
        op,
        cfg.repo,
        min_version=cfg.gh_min_tuple,
        timeout=getattr(cfg, "gh_timeout_sec", 30),
        retries=getattr(cfg, "gh_retries", 0),
        **kwargs,  # type: ignore[arg-type]
    )


def _list_with_more(cfg: AgenticConfig, op: str, limit: int = _DEFAULT_LIST_LIMIT) -> dict:
    """Fetch a capped list plus a ``has_more`` signal.

    Requests ``limit + 1`` rows: if the extra row comes back, more exist beyond
    the cap, so the caller knows the shortlist is partial. Returns
    ``{"items": [...], "count": N, "has_more": bool}`` (items trimmed to ``limit``).
    """
    data = _read(cfg, op, limit=limit + 1)["data"]
    items = data if isinstance(data, list) else []
    has_more = len(items) > limit
    trimmed = items[:limit]
    return {"items": trimmed, "count": len(trimmed), "has_more": has_more}


def fetch_pr_context(
    cfg: AgenticConfig, number: int, *, include_diff: bool = True, app_cfg: dict | None = None
) -> dict:
    """Return a PR's metadata and (optionally) its diff as one bundle.

    *app_cfg* is the full application config dict (the ``banned_patterns`` source
    for the injection scan); it defaults to the on-disk config.
    """
    _guard_read_op(cfg, "pr_view")
    bundle: dict = {"repo": cfg.repo, "number": number}
    pr = _read(cfg, "pr_view", number=number)["data"]
    bundle["pr"] = pr
    fields = _text_fields(pr, "pr", ("title", "body"))
    if include_diff:
        _guard_read_op(cfg, "pr_diff")
        diff = _read(cfg, "pr_diff", number=number)["diff"]
        bundle["diff"] = diff
        fields.append(("diff", diff))
    bundle["governance_findings"] = _injection_findings(app_cfg, cfg.repo, number, fields)
    return bundle


def fetch_issue_context(cfg: AgenticConfig, number: int, *, app_cfg: dict | None = None) -> dict:
    """Return an issue's metadata bundle. See :func:`fetch_pr_context` for *app_cfg*."""
    _guard_read_op(cfg, "issue_view")
    issue = _read(cfg, "issue_view", number=number)["data"]
    fields = _text_fields(issue, "issue", ("title", "body"))
    # issue_view requests a `comments` field, so comment bodies arrive in the same
    # payload and are just as attacker-authored as the issue body itself.
    if isinstance(issue, dict):
        for i, comment in enumerate(issue.get("comments") or []):
            fields.extend(_text_fields(comment, f"issue.comments[{i}]", ("body",)))
    return {
        "repo": cfg.repo,
        "number": number,
        "issue": issue,
        "governance_findings": _injection_findings(app_cfg, cfg.repo, number, fields),
    }


def fetch_pr_list(cfg: AgenticConfig, max_items: int = 100) -> dict:
    """Return open PRs as ``{"items", "count", "has_more"}`` up to *max_items*.

    ``has_more=True`` signals that additional PRs exist beyond the returned slice;
    the caller can page by increasing *max_items* or calling the gh CLI directly.
    """
    _guard_read_op(cfg, "pr_list")
    return _list_with_more(cfg, "pr_list", limit=max_items)


def fetch_issue_list(cfg: AgenticConfig, max_items: int = 100) -> dict:
    """Return open issues as ``{"items", "count", "has_more"}`` up to *max_items*.

    Same pagination semantics as :func:`fetch_pr_list`.
    """
    _guard_read_op(cfg, "issue_list")
    return _list_with_more(cfg, "issue_list", limit=max_items)


def fetch_repo_context(
    cfg: AgenticConfig,
    *,
    max_prs: int = _DEFAULT_LIST_LIMIT,
    max_issues: int = _DEFAULT_LIST_LIMIT,
    app_cfg: dict | None = None,
) -> dict:
    """Return a repo overview plus a shortlist of open PRs and issues.

    *max_prs* and *max_issues* cap the number of items in each shortlist
    (default ``_DEFAULT_LIST_LIMIT``).  The shortlists are returned as
    ``{"items", "count", "has_more"}`` so a consumer can tell a complete list
    from a truncated one (``has_more=True`` means more exist beyond the cap).
    """
    _guard_read_op(cfg, "repo_view")
    bundle: dict = {"repo": cfg.repo}
    overview = _read(cfg, "repo_view")["data"]
    bundle["overview"] = overview
    fields = _text_fields(overview, "overview", ("description",))
    if "pr_list" in cfg.allowed_read_ops:
        bundle["open_prs"] = _list_with_more(cfg, "pr_list", limit=max_prs)
        fields.extend(_shortlist_title_fields(bundle["open_prs"], "open_prs"))
    if "issue_list" in cfg.allowed_read_ops:
        bundle["open_issues"] = _list_with_more(cfg, "issue_list", limit=max_issues)
        fields.extend(_shortlist_title_fields(bundle["open_issues"], "open_issues"))
    bundle["governance_findings"] = _injection_findings(app_cfg, cfg.repo, None, fields)
    return bundle


__all__ = [
    "DEFAULT_MIN_GH",
    "INJECTION_FINDING_CODE",
    "SCANNER_UNAVAILABLE_CODE",
    "fetch_issue_context",
    "fetch_issue_list",
    "fetch_pr_context",
    "fetch_pr_list",
    "fetch_repo_context",
]
