"""Model boundary must count the served template and reject silent context loss."""

from pathlib import Path

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser
from inspect_ai.tool import ToolCall, ToolCallError, ToolInfo

from context_fidelity.adapters.model import (
    ContextOverflow,
    OfficialTokenizer,
    ensure_context_fits,
    local_model,
)


class TemplateBackend:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.tools: list[dict[str, object]] = []

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert add_special_tokens is False
        return list(range(len(text.split())))

    def apply_chat_template(
        self,
        conversation: list[dict[str, object]],
        *,
        tools: list[dict[str, object]] | None,
        tokenize: bool,
        add_generation_prompt: bool,
    ) -> list[int]:
        assert tokenize and add_generation_prompt
        self.messages = conversation
        self.tools = tools or []
        return [1] * (len(conversation) * 3 + len(self.tools) * 10)


def test_template_receives_roles_arguments_errors_and_full_tool_schema() -> None:
    backend = TemplateBackend()
    tokenizer = OfficialTokenizer(backend)
    messages = [
        ChatMessageUser(content="Task"),
        ChatMessageAssistant(
            content="checking",
            tool_calls=[ToolCall(id="c1", function="read_file", arguments={"path": "solution.py"})],
        ),
        ChatMessageTool(
            content="",
            function="read_file",
            tool_call_id="c1",
            error=ToolCallError("parsing", "bad arguments"),
        ),
    ]
    assert tokenizer.count("two words") == 2
    assert (
        tokenizer.count_messages(messages, [ToolInfo(name="read_file", description="Read")]) == 19
    )
    assert backend.messages[0] == {"role": "user", "content": "Task"}
    assert backend.messages[1]["tool_calls"] == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "read_file", "arguments": {"path": "solution.py"}},
        }
    ]
    assert backend.messages[2] == {
        "role": "tool",
        "content": "Error: bad arguments",
        "tool_call_id": "c1",
    }
    assert backend.tools[0]["type"] == "function"
    assert "parameters" in backend.tools[0]["function"]


def test_budget_reserves_output_tokens_and_never_mutates_messages() -> None:
    tokenizer = OfficialTokenizer(TemplateBackend())
    messages = [ChatMessageUser(content="Task")]
    assert ensure_context_fits(tokenizer, messages, [], max_context_tokens=8, output_tokens=5) == 3
    with pytest.raises(ContextOverflow, match="9.*8"):
        ensure_context_fits(tokenizer, messages, [], max_context_tokens=8, output_tokens=6)
    assert messages[0].text == "Task"
    with pytest.raises(ValueError, match="positive"):
        ensure_context_fits(tokenizer, messages, [], max_context_tokens=0, output_tokens=1)


def test_local_provider_uses_dummy_key_and_disallows_public_endpoints() -> None:
    model = local_model("http://127.0.0.1:18081/v1")
    assert model.api.api_key == "local-unused"
    assert model.api.base_url == "http://127.0.0.1:18081/v1"
    for endpoint in [
        "https://api.openai.com/v1",
        "http://user:secret@localhost:1/v1",
        "file:///tmp/x",
    ]:
        with pytest.raises(ValueError, match="loopback"):
            local_model(endpoint)


def test_tokenizer_loader_pins_revision_disables_remote_code_and_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    def load(model_id: str, **kwargs: object) -> TemplateBackend:
        observed.update(model_id=model_id, **kwargs)
        return TemplateBackend()

    monkeypatch.setattr("transformers.AutoTokenizer.from_pretrained", load)
    tokenizer = OfficialTokenizer.load(cache_dir=tmp_path)
    assert tokenizer.count("hello") == 1
    assert observed == {
        "model_id": "Qwen/Qwen3-4B-Instruct-2507",
        "revision": "cdbee75f17c01a7cc42f958dc650907174af0554",
        "cache_dir": str(tmp_path),
        "trust_remote_code": False,
        "local_files_only": True,
    }


def test_invalid_tokenizer_and_negative_counts_cannot_bypass_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("transformers.AutoTokenizer.from_pretrained", lambda *a, **k: object())
    with pytest.raises(TypeError, match="tokenizer lacks"):
        OfficialTokenizer.load(cache_dir=tmp_path)

    class InvalidCounter:
        def count(self, text: str) -> int:
            return -1

        def count_messages(self, messages, tools=()) -> int:
            return -1

    with pytest.raises(ValueError, match="nonnegative"):
        ensure_context_fits(InvalidCounter(), [], [], max_context_tokens=3, output_tokens=1)


def test_unsupported_multimodal_content_fails_and_normal_tool_content_is_preserved() -> None:
    from inspect_ai.model import ContentImage

    backend = TemplateBackend()
    tokenizer = OfficialTokenizer(backend)
    with pytest.raises(ValueError, match="text-only"):
        tokenizer.count_messages(
            [ChatMessageUser(content=[ContentImage(image="https://x.test/a")])]
        )
    tokenizer.count_messages([ChatMessageTool(content="exact output", tool_call_id="c1")])
    assert backend.messages[0]["content"] == "exact output"


def test_tool_schema_matches_pinned_inspect_provider_wire_conversion() -> None:
    # Characterize the installed provider at its real conversion boundary, without HTTP.
    from inspect_ai.model._providers.openai_compatible import OpenAICompatibleAPI
    from inspect_ai.tool import ToolParam, ToolParams

    info = ToolInfo(
        name="write_file",
        description="Write",
        parameters=ToolParams(
            properties={"content": ToolParam(type="string", description="Exact content")},
            required=["content"],
        ),
    )
    provider = OpenAICompatibleAPI(
        "local/qwen", base_url="http://127.0.0.1:1/v1", api_key="local-unused"
    )
    backend = TemplateBackend()
    OfficialTokenizer(backend).count_messages([ChatMessageUser(content="Task")], [info])
    assert backend.tools == provider.tools_to_openai([info])


def test_local_provider_makes_one_sdk_request_with_exact_model_and_timeout() -> None:
    import asyncio
    import json

    import httpx2
    from inspect_ai.model import GenerateConfig
    from openai import DefaultAsyncHttpxClient, InternalServerError

    from context_fidelity.adapters.model import MODEL_ID

    model = local_model("http://127.0.0.1:1/v1")
    api = model.api
    if hasattr(api, "_resolve_server"):
        api._resolve_server()  # Characterize the former lazy vLLM initialization offline.
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["model"] == MODEL_ID
        return httpx2.Response(500, json={"error": {"message": "synthetic failure"}})

    async def probe() -> None:
        original = api.client._client
        api.client._client = DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
        try:
            with pytest.raises(InternalServerError):
                await api._generate_completion(
                    {
                        "model": api.service_model_name(),
                        "messages": [{"role": "user", "content": "Synthetic probe"}],
                        "max_tokens": 1,
                    },
                    GenerateConfig(max_retries=0, timeout=90, max_tokens=1),
                )
        finally:
            await api.client.close()
            await original.aclose()

    asyncio.run(probe())
    assert len(requests) == 1
    assert api.client.max_retries == 0
    assert api.client.timeout == 90
    assert requests[0].extensions["timeout"]["read"] == 90
