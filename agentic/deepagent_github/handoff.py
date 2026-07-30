"""Sanitized context handoff for the agentic plane's cloud egress.

The point of this module is that a cloud model call is audited *as egress*, not
merely as a model call. Before any bytes leave the machine, the outbound prompt
is injection-checked, redacted, hashed, and recorded -- so the audit log can
answer "what left, when, to whom, and how much of it was redacted" without ever
storing the prompt itself.

Two limitations to state plainly to anyone this evidence is shown to:
(1) redaction is pattern-based. It removes recognizable secret and PII SHAPES and
    cannot certify that free text contains no confidential meaning. Restricting
    WHICH documents are eligible for cloud context is the stronger control.
(2) once bytes reach a provider, retention and processing are governed by that
    provider's agreement, not by CyClaw. That is why the per-run confirmation
    gate exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from utils.logger import audit_log, hash_query, redact_sensitive
from utils.sanitizer import check_input

HANDOFF_EVENT = "agentic_deepagent_cloud_handoff"


@dataclass(frozen=True)
class HandoffEnvelope:
    """What is about to leave the machine, recorded before it leaves."""

    provider: str
    prompt_sha256: str
    prompt_chars: int
    context_doc_ids: tuple[str, ...]
    redactions_applied: int


def sanitize_handoff(
    prompt: str,
    *,
    provider: str,
    doc_ids: tuple[str, ...] = (),
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> tuple[str, HandoffEnvelope]:
    """Injection-check, redact, and audit an outbound prompt. Returns the redacted text.

    Raises ``PromptInjectionError`` when the prompt matches a banned pattern or
    exceeds the length cap. That is deliberately a HARD failure here, unlike the
    advisory findings ``agentic/context.py`` attaches to inbound GitHub text: this
    is content CyClaw is about to *send*, and a prompt carrying an injection shape
    is precisely what must not be forwarded to a third party under the operator's
    credentials.
    """
    checked = check_input(prompt, config_path)
    redacted = redact_sensitive(checked, cfg)
    envelope = HandoffEnvelope(
        provider=provider,
        # The hash, never the text. Same discipline as the audit log's query_hash:
        # it proves which payload was sent without persisting it.
        prompt_sha256=hash_query(redacted),
        prompt_chars=len(redacted),
        context_doc_ids=tuple(doc_ids),
        redactions_applied=int(redacted != checked),
    )
    audit_log({"event": HANDOFF_EVENT, **asdict(envelope)}, config_path=config_path, cfg=cfg)
    return redacted, envelope


__all__ = ["HANDOFF_EVENT", "HandoffEnvelope", "sanitize_handoff"]
