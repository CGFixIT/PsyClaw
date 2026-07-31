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

Gating (``mode == "hybrid"``, ``providers.<name>.enabled``,
``allow_cloud_providers``, the per-run online confirmation, and the API key)
is entirely the CALLER's responsibility -- exactly like ``LocalProposerClient``,
this class only egresses; it never decides whether it is allowed to. Callers
construct it from an already-gated ``DeepAgentModelSettings`` (see
``DeepAgentModelSettings.from_config``'s own gate-3/4 assertion, and
``build_chat_model``'s own gate-5 key check).

Every outbound user prompt is scanned and redacted via
``agentic.deepagent_github.handoff.sanitize_handoff`` before it leaves the
process -- that call itself records the ``agentic_deepagent_cloud_handoff``
audit event, so this module does not duplicate it.

Speculating (flagged per this repo's own confabulation-risk discipline):
neither ``langchain-xai`` nor ``langchain-anthropic`` is installed in the
sandbox this was written in, so the exact request/response shape each
provider's ``BaseChatModel`` subclass builds from ``max_tokens``/``temperature``
invoke-time kwargs could not be exercised end-to-end here. What IS verified by
direct import in this same sandbox (``langchain-core==1.4.8``, a mandatory
base dependency already used by ``graph.py`` -- not an optional extra):
``BaseChatModel.invoke(input, config=None, *, stop=None, **kwargs) -> AIMessage``,
where ``input`` accepts a plain ``Sequence[BaseMessage]`` and ``**kwargs``
forward to the provider's own ``_generate``; and ``BaseMessage.content: str |
list[str | dict[Any, Any]]``, which is why ``_coerce_text_content`` below does
not assume a plain string. Treat the provider-specific translation of
``max_tokens``/``temperature`` as unverified until a live-fire test runs
against real ``ChatXAI``/``ChatAnthropic`` instances.
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
            raise AgenticError(
                "cloud proposer invocation failed",
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
