"""Wire-format checks for the real ChatXAI/ChatAnthropic classes.

agentic.deepagent_github.chat_client.ChatModelProposerClient assumes two facts
about the LangChain chat models agentic.deepagent_github.model_adapter.build_chat_model
constructs: that invoke-time max_tokens/temperature kwargs reach the actual
outbound request payload (not just a constructor-time default), and that an
AIMessage's content can come back as a list of blocks rather than a plain str
(hence _coerce_text_content). Both facts were assumed correct based on
langchain-core's public contract but could not be checked against the real
provider SDKs without them installed -- this file is that check, run only
when the real optional packages are present (the deepagents-harness CI lane
installs them; see .github/workflows/ci.yml).

No network is used: ChatXAI is driven through an injected httpx.MockTransport,
and ChatAnthropic through a fake ``_client`` standing in for its cached
anthropic.Client property -- both intercept the request one layer below the
real HTTP call, so the exact payload the SDK was about to send is captured
directly rather than inferred from source reading alone.
"""

from __future__ import annotations

import json

import httpx
import pytest

pytest.importorskip("langchain_xai")
pytest.importorskip("langchain_anthropic")

# E402 is intentional and load-bearing, matching test_agentic_deepagent_optional.py:
# these imports MUST follow the importorskip calls above, or collecting this file in
# a lane without the optional dependencies raises ImportError instead of skipping.
from anthropic.types import Message, TextBlock, Usage  # noqa: E402
from langchain_anthropic import ChatAnthropic  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_xai import ChatXAI  # noqa: E402

from agentic.deepagent_github.chat_client import _coerce_text_content  # noqa: E402


def test_grok_invoke_kwargs_reach_the_real_request_body():
    """max_tokens/temperature passed to .invoke() must land in the JSON body.

    ChatModelProposerClient.invoke() calls model.invoke(messages, max_tokens=...,
    temperature=...) and relies on the library forwarding those straight
    through -- this is the "unverified in this sandbox" claim from the class's
    own docstring, now checked against the real ChatXAI class.
    """
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "grok-4.5",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport = httpx.MockTransport(handler)
    model = ChatXAI(model="grok-4.5", api_key="fake-key", http_client=httpx.Client(transport=transport))

    response = model.invoke(
        [SystemMessage(content="sys"), HumanMessage(content="hi")], max_tokens=777, temperature=0.33
    )

    assert response.content == "ok"
    assert captured["body"]["max_tokens"] == 777
    assert captured["body"]["temperature"] == 0.33


def test_claude_invoke_kwargs_reach_the_real_request_payload():
    """Same claim, checked against the real ChatAnthropic class.

    ChatAnthropic builds its Anthropic SDK client via a cached_property
    (``_client``), not a constructor kwarg the way ChatXAI does -- so the
    interception point here is a fake client standing in for that property,
    one layer below the real anthropic.Client.messages.create(**payload) call.
    """
    captured = {}

    class _FakeMessages:
        def create(self, **payload):
            captured["payload"] = payload
            raise RuntimeError("stop here -- payload already captured")

    class _FakeClient:
        def __init__(self):
            self.messages = _FakeMessages()

    model = ChatAnthropic(model="claude-sonnet-5", api_key="fake-key")
    object.__setattr__(model, "__dict__", {**model.__dict__, "_client": _FakeClient()})

    with pytest.raises(RuntimeError, match="stop here"):
        model.invoke([SystemMessage(content="sys"), HumanMessage(content="hi")], max_tokens=777, temperature=0.33)

    assert captured["payload"]["max_tokens"] == 777
    assert captured["payload"]["temperature"] == 0.33


def test_claude_multi_block_content_is_coerced_to_plain_text():
    """A multi-block Claude reply is a real, reachable AIMessage.content shape.

    _coerce_text_content exists because BaseMessage.content is typed
    ``str | list[str | dict[Any, Any]]``, not always a plain string -- this
    proves that shape is not merely a type annotation nobody ever hits: a
    two-block text response from the real anthropic SDK types produces
    exactly the ``[{"type": "text", "text": ...}, ...]`` list this loop's
    coercion helper is written to handle.
    """

    class _FakeMessages:
        def create(self, **_payload):
            return Message(
                id="msg_test",
                type="message",
                role="assistant",
                model="claude-sonnet-5",
                content=[
                    TextBlock(type="text", text="part one"),
                    TextBlock(type="text", text="part two"),
                ],
                stop_reason="end_turn",
                stop_sequence=None,
                usage=Usage(input_tokens=1, output_tokens=1),
            )

    class _FakeClient:
        def __init__(self):
            self.messages = _FakeMessages()

    model = ChatAnthropic(model="claude-sonnet-5", api_key="fake-key")
    object.__setattr__(model, "__dict__", {**model.__dict__, "_client": _FakeClient()})

    response = model.invoke([SystemMessage(content="s"), HumanMessage(content="hi")])

    assert isinstance(response.content, list)
    assert _coerce_text_content(response.content) == "part one\npart two"
