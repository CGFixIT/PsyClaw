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

from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from agentic.deepagent_github.handoff import sanitize_handoff
from agentic.deepagent_github.model_adapter import DeepAgentModelSettings, build_chat_model
from utils.errors import AgenticError, PromptInjectionError
from utils.logger import audit_log


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
        temperature: float = 0.0,
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

        model = build_chat_model(self.settings)
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=sanitized_prompt)]
        try:
            ai_message = model.invoke(messages, max_tokens=max_tokens, temperature=temperature)
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
            # Type name in the MESSAGE, for the same reason LocalProposerClient
            # does it: agentic/cli.py prints exc.message alone and persists it
            # as the run record's `error`, so details={} never reaches an
            # operator. Deliberately NOT annotated with a timeout budget the
            # way the local client's is -- the concrete exception types belong
            # to whichever SDK is active (none importable here, hence the bare
            # `except Exception` above), so this cannot tell a timeout from a
            # rate limit without guessing.
            raise AgenticError(
                f"cloud proposer invocation failed ({type(exc).__name__})",
                details={"provider": provider, "error_type": type(exc).__name__},
            ) from exc

        content = _coerce_text_content(ai_message.content)
        audit_log(
            {"event": "agentic_deepagent_cloud_model_succeeded", "provider": provider, "model": self.settings.model},
            config_path=config_path,
            cfg=cfg,
        )
        return ChatModelProposerResponse(content=content, model=self.settings.model, provider=provider)


__all__ = ["ChatModelProposerClient", "ChatModelProposerResponse"]
