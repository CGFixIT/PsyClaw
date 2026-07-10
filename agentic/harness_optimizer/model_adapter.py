"""Local LM Studio/OpenAI-compatible proposer adapter for the harness optimizer."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx

from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools
from utils.errors import AgenticError
from utils.logger import audit_log


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LocalProposerResponse:
    """Structured response from the local proposer model."""

    content: str
    model: str
    provider: str = "lmstudio"


class LocalProposerClient:
    """Minimal OpenAI-compatible chat client for local proposer use.

    The constructor accepts an httpx transport so tests use MockTransport and
    never require live LM Studio.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_sec: float = 30.0,
        api_key: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key.strip()
        self._client = httpx.Client(timeout=timeout_sec, transport=transport)

    def close(self) -> None:
        self._client.close()

    def invoke(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        config_path: str = "config.yaml",
        cfg: dict | None = None,
    ) -> LocalProposerResponse:
        if not self.model.strip():
            raise AgenticError("local proposer model must be configured before invocation")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        audit_log(
            {
                "event": "agentic_harness_proposer_model_invoked",
                "provider": "lmstudio",
                "model": self.model,
                "system_prompt_hash": _hash_text(system_prompt),
                "user_prompt_hash": _hash_text(user_prompt),
            },
            config_path=config_path,
            cfg=cfg,
        )
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            audit_log(
                {
                    "event": "agentic_harness_proposer_model_failed",
                    "provider": "lmstudio",
                    "model": self.model,
                    "error_type": type(exc).__name__,
                },
                config_path=config_path,
                cfg=cfg,
            )
            raise AgenticError(
                "local proposer invocation failed",
                details={"error_type": type(exc).__name__},
            ) from exc
        if not isinstance(content, str) or not content.strip():
            audit_log(
                {
                    "event": "agentic_harness_proposer_model_failed",
                    "provider": "lmstudio",
                    "model": self.model,
                    "error_type": "AgenticError",
                },
                config_path=config_path,
                cfg=cfg,
            )
            raise AgenticError("local proposer returned empty content")
        audit_log(
            {
                "event": "agentic_harness_proposer_model_succeeded",
                "provider": "lmstudio",
                "model": self.model,
            },
            config_path=config_path,
            cfg=cfg,
        )
        return LocalProposerResponse(content=content, model=self.model)


def invoke_workspace_proposer(
    *,
    client: LocalProposerClient,
    tools: ProposerWorkspaceTools,
    instruction: str,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> dict:
    """Ask the local proposer for a proposal and persist proposal.md explicitly."""

    manifest = tools.read_surface_manifest()
    train = tools.read_train_failures()
    history = tools.read_visible_history()
    user_prompt = "\n\n".join(
        (
            f"Instruction:\n{instruction}",
            f"Surface manifest:\n{manifest}",
            f"Visible train failures:\n{train}",
            f"Visible history:\n{history}",
        )
    )
    response = client.invoke(
        system_prompt="You propose governed CyClaw harness improvements. Return proposal markdown only.",
        user_prompt=user_prompt,
        config_path=config_path,
        cfg=cfg,
    )
    proposal = tools.finish_proposal(response.content)
    return {"model": response.model, "proposal": proposal}
