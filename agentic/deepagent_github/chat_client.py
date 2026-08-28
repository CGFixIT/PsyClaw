"""Cloud-parity ``ProposerClient`` wrapping a gated chat model.

``agentic.real_repo_loop.run_real_repo_loop`` accepts anything satisfying
``ProposerClient`` (a ``Protocol``: ``invoke(...)``/``close()``); this module is
the second implementation of that contract, alongside
``agentic.harness_optimizer.model_adapter.LocalProposerClient``. Where the local
client is a direct httpx POST to an OpenAI-compatible ``/chat/completions``
endpoint, this one drives a LangChain ``BaseChatModel`` built by
``agentic.deepagent_github.model_adapter.build_chat_model`` -- the same
provider-gated construction the (still-dormant) deepagents harness already
uses, so the same six-gate cloud-provider chain (see ``docs/THREAT_MODEL.md``)
can cover a real git-commit loop, not only that unexercised graph.

Gating (``agentic.enabled``, ``deepagent_github.enabled``,
``allow_cloud_providers``, ``providers.<name>.enabled``, the provider's API
key, and the per-run ``--confirm-online``) is entirely the CALLER's
responsibility. Note those first two: an earlier version of this docstring
said ``mode == "hybrid"``, which is the CORE GRAPH's I3 gate and is read by
nothing under ``agentic/`` -- an operator treating ``app.mode: offline`` as a
global egress kill switch would have been wrong for this plane -- exactly like ``LocalProposerClient``,
this class only egresses; it never decides whether it is allowed to. Callers
construct it from an already-gated ``DeepAgentModelSettings`` (see
``DeepAgentModelSettings.from_config``'s own gate-3/4 assertion, and
``build_chat_model``'s own gate-5 key check).

Every outbound user prompt is scanned and redacted via
``agentic.deepagent_github.handoff.sanitize_handoff`` before it leaves the
process -- that call itself records the ``agentic_deepagent_cloud_handoff``
audit event, so this module does not duplicate it.

Verified (not merely read from source): the exact pinned versions
(``langchain-xai==1.2.2``, ``langchain-anthropic==1.4.8``) were installed into
an isolated venv and driven end-to-end with an injected transport/fake client
-- no network, no provider account. Both confirm ``max_tokens``/``temperature``
passed as ``.invoke()`` kwargs reach the real outbound payload: ``ChatXAI``
inherits ``langchain_openai.BaseChatOpenAI._get_request_payload``, which does
``payload = {**self._default_params, **kwargs}`` (invoke-time kwargs win);
``ChatAnthropic._get_request_payload`` builds its payload the same way and
passes it to ``self._client.messages.create(**payload)``. Also confirmed: a
single-block Claude reply's ``AIMessage.content`` is a plain ``str``, but a
multi-block reply's is a real ``list[dict]`` shaped
``[{"type": "text", "text": ...}, ...]`` -- exactly what
``_coerce_text_content`` below is written to handle, not a hypothetical. See
``tests/test_agentic_cloud_chat_model_wire_format.py``, which runs this same
check as a permanent regression test in the ``deepagents-harness`` CI lane
(the only one with both optional SDKs installed) rather than a one-off manual
verification that would otherwise evaporate.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from langchain_core.messages import HumanMessage, SystemMessage

from agentic.deepagent_github.handoff import sanitize_handoff
from agentic.deepagent_github.model_adapter import DeepAgentModelSettings, build_chat_model
from utils.errors import AgenticError, PromptInjectionError
from utils.logger import audit_log
from utils.spend import record_external_usage

logger = logging.getLogger("cyclaw.agentic.chat_client")

# Last 2xx JSON ``usage`` from the Grok httpx hook. Prefer this over LangChain
# metadata so xAI ``cost_in_usd_ticks`` is not dropped.
_HTTP_USAGE: contextvars.ContextVar[dict[str, object] | None] = contextvars.ContextVar(
    "cyclaw_agentic_http_usage", default=None
)


@dataclass(frozen=True)
class ChatModelProposerResponse:
    """Structured response from a cloud-backed proposer model."""

    content: str
    model: str
    provider: str


def _coerce_text_content(content: str | list[object]) -> str:
    """Normalize a chat model's response content to plain text.

    Most providers return a plain ``str``. Some return a list of content
    blocks instead (see this module's docstring for the verified
    ``BaseMessage.content`` type) -- this loop's downstream parsing
    (``agentic.real_repo_loop._parse_file_blocks``) expects plain text, so
    text-shaped blocks are joined; a block that isn't a recognizable text
    shape (e.g. a tool-call block) is skipped rather than guessed at.
    """
    if isinstance(content, str):
        return content
    parts = [
        block if isinstance(block, str) else block["text"]
        for block in content
        if isinstance(block, str) or (isinstance(block, dict) and isinstance(block.get("text"), str))
    ]
    return "\n".join(parts)


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump()
        return dumped if isinstance(dumped, dict) else None
    return None


def _usage_from_langchain_metadata(provider: str, usage_meta: Mapping[str, object]) -> dict[str, object]:
    """Map LangChain ``usage_metadata`` onto vendor-shaped usage dicts."""
    input_details = _as_mapping(usage_meta.get("input_token_details")) or {}
    output_details = _as_mapping(usage_meta.get("output_token_details")) or {}
    if provider == "claude":
        return {
            "input_tokens": usage_meta.get("input_tokens"),
            "output_tokens": usage_meta.get("output_tokens"),
            "cache_creation_input_tokens": input_details.get("cache_creation"),
            "cache_read_input_tokens": input_details.get("cache_read"),
        }
    return {
        "prompt_tokens": usage_meta.get("input_tokens"),
        "completion_tokens": usage_meta.get("output_tokens"),
        "prompt_tokens_details": {
            "cached_tokens": input_details.get("cache_read", input_details.get("cached_tokens")),
        },
        "completion_tokens_details": {
            "reasoning_tokens": output_details.get("reasoning", output_details.get("reasoning_tokens")),
        },
    }


def _capture_http_usage(response: httpx.Response) -> None:
    """Store vendor usage JSON. Never log the body.

    httpx fires response event hooks before the body is read (`Client.send`
    reads it only afterward), so `.json()` here needs an explicit `.read()`
    first -- omitting it raises `ResponseNotRead` on every real request and
    is silently swallowed by the except below, making this a no-op that only
    "works" against a pre-read `httpx.Response(json=...)` test fixture.
    """
    try:
        if response.status_code < 200 or response.status_code >= 300:
            return
        response.read()
        data = response.json()
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            _HTTP_USAGE.set(usage)
    except Exception as exc:
        logger.debug("http usage capture failed: %s", type(exc).__name__)
        return


def _usage_capturing_http_client(timeout_sec: int) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_sec,
        trust_env=False,
        event_hooks={"response": [_capture_http_usage]},
    )


def _usage_from_ai_message(provider: str, message: object) -> object | None:
    """Prefer vendor-shaped ``response_metadata`` so xAI ticks survive if forwarded."""
    meta = _as_mapping(getattr(message, "response_metadata", None))
    if meta is not None:
        if provider == "claude":
            usage = _as_mapping(meta.get("usage"))
            if usage is not None:
                return usage
        else:
            for key in ("token_usage", "usage"):
                usage = _as_mapping(meta.get(key))
                if usage is not None:
                    return usage
    usage_meta = _as_mapping(getattr(message, "usage_metadata", None))
    if usage_meta is not None:
        return _usage_from_langchain_metadata(provider, usage_meta)
    return None


def _spend_file_from_cfg(cfg: dict | None) -> Path | None:
    if not isinstance(cfg, dict):
        return None
    logging_cfg = cfg.get("logging")
    if not isinstance(logging_cfg, dict):
        return None
    raw = logging_cfg.get("spend_file")
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    return None


def _usage_for_spend(provider: str, message: object) -> object | None:
    captured = _HTTP_USAGE.get()
    if provider == "grok" and isinstance(captured, dict):
        return captured
    return _usage_from_ai_message(provider, message)


def _record_proposer_spend(provider: str, model: str, message: object, cfg: dict | None) -> None:
    try:
        record_external_usage(
            provider=provider,
            model=model,
            usage=_usage_for_spend(provider, message),
            source="agentic",
            spend_file=_spend_file_from_cfg(cfg),
        )
    except Exception as exc:
        logger.debug("spend record failed: %s", type(exc).__name__)


@dataclass(frozen=True)
class ChatModelProposerClient:
    """A ``ProposerClient`` backed by a gated cloud chat model (Grok or Claude).

    Satisfies ``agentic.real_repo_loop.ProposerClient`` structurally -- see
    that module for why the contract is a ``Protocol`` rather than a shared
    base class.
    """

    settings: DeepAgentModelSettings

    def close(self) -> None:
        """No persistent resource to release.

        Unlike ``LocalProposerClient`` (which owns an ``httpx.Client``), the
        LangChain chat model classes this wraps (``ChatOpenAI``/``ChatXAI``/
        ``ChatAnthropic``) manage their own HTTP client lifetime internally and
        expose no ``close()`` of their own to call here.
        """

    def invoke(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2048,
        temperature: float | None = 0.0,
        config_path: str = "config.yaml",
        cfg: dict | None = None,
    ) -> ChatModelProposerResponse:
        provider = self.settings.provider
        try:
            sanitized_prompt, _envelope = sanitize_handoff(
                user_prompt, provider=provider, config_path=config_path, cfg=cfg,
                max_chars=self.settings.max_handoff_chars,
            )
        except PromptInjectionError as exc:
            # sanitize_handoff's own hard fail: unlike agentic/context.py's
            # advisory-only inbound scan, an outbound prompt matching a banned
            # pattern must never reach a third party. Re-raised as AgenticError
            # because that is the type every real-repo-loop call site (and
            # agentic.cli's exception handling around it) already catches --
            # PromptInjectionError is rooted at RAGError, not AgenticError, and
            # nothing upstream of this client catches RAGError.
            raise AgenticError(
                f"outbound prompt to {provider} blocked by the injection scan: {exc.message}",
                details={"provider": provider},
            ) from exc

        http_client: httpx.Client | None = None
        usage_token = _HTTP_USAGE.set(None)
        try:
            if provider == "grok":
                http_client = _usage_capturing_http_client(self.settings.timeout_sec)
                model = build_chat_model(self.settings, http_client=http_client)
            else:
                model = build_chat_model(self.settings)
            messages = [SystemMessage(content=system_prompt), HumanMessage(content=sanitized_prompt)]
            # Anthropic REJECTS a non-default temperature outright on the Claude 5
            # family: "non-default temperature, top_p, or top_k values return a 400
            # error on every request, regardless of whether thinking is used"
            # (platform.claude.com/docs/en/build-with-claude/thinking, verified
            # 2026-08-02), and the Messages API default is 1.0 -- so the 0.0 this
            # method used to send unconditionally made EVERY Claude plan call fail
            # 400 on the shipped providers.claude.model "claude-sonnet-5". The core
            # RAG path already knew this (llm/client.py's ClaudeClient omits
            # temperature while GrokClient sends it, pinned by
            # tests/test_client.py's "temperature not in json" assertion); this
            # path had simply not inherited the rule. Dropped only for Claude:
            # xAI's OpenAI-compatible surface still wants it, and passing None
            # explicitly lets a caller opt out for any provider.
            invoke_kwargs: dict[str, object] = {"max_tokens": max_tokens}
            if temperature is not None and provider != "claude":
                invoke_kwargs["temperature"] = temperature
            try:
                ai_message = model.invoke(messages, **invoke_kwargs)
            except Exception as exc:
                # The concrete exception type depends on which SDK is active
                # (openai/xai/anthropic clients, none importable here to enumerate) --
                # mirrors llm/client.py's own "log only the type, never the message"
                # discipline, since a provider error message can echo request content.
                audit_log(
                    {
                        "event": "agentic_deepagent_cloud_model_failed",
                        "provider": provider,
                        "model": self.settings.model,
                        "error_type": type(exc).__name__,
                    },
                    config_path=config_path,
                    cfg=cfg,
                )
                raise AgenticError(
                    f"cloud proposer invocation failed ({type(exc).__name__})",
                    details={"provider": provider, "error_type": type(exc).__name__},
                ) from exc

            try:
                content = _coerce_text_content(ai_message.content)
            finally:
                # Billed 200 still consumes quota even if content coercion later raises.
                _record_proposer_spend(provider, self.settings.model, ai_message, cfg)
            audit_log(
                {
                    "event": "agentic_deepagent_cloud_model_succeeded",
                    "provider": provider,
                    "model": self.settings.model,
                },
                config_path=config_path,
                cfg=cfg,
            )
            return ChatModelProposerResponse(content=content, model=self.settings.model, provider=provider)
        finally:
            _HTTP_USAGE.reset(usage_token)
            if http_client is not None:
                http_client.close()


__all__ = ["ChatModelProposerClient", "ChatModelProposerResponse"]
