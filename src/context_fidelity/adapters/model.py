"""Pinned local model and chat-template accounting at the provider boundary."""

from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlsplit

from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageTool,
    Model,
    get_model,
)
from inspect_ai.tool import ToolInfo

MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"


class ContextOverflow(ValueError):
    """The complete prompt plus reserved generation cannot fit the model."""


class Tokenizer(Protocol):
    def count(self, text: str) -> int: ...

    def count_messages(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolInfo] = ()
    ) -> int: ...


class ChatTokenizer(Protocol):
    """Small interface to the official tokenizer; no model weights are loaded."""

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]: ...

    def apply_chat_template(
        self,
        conversation: list[dict[str, object]],
        *,
        tools: list[dict[str, object]] | None,
        tokenize: bool,
        add_generation_prompt: bool,
    ) -> list[int]: ...


class OfficialTokenizer:
    def __init__(self, backend: ChatTokenizer) -> None:
        self.backend = backend

    @classmethod
    def load(
        cls,
        *,
        cache_dir: Path,
        model_id: str = MODEL_ID,
        revision: str = MODEL_REVISION,
        local_files_only: bool = True,
    ) -> "OfficialTokenizer":
        # Transformers' untyped factory is contained at this validated boundary.
        module = import_module("transformers")
        backend: object = module.AutoTokenizer.from_pretrained(
            model_id,
            revision=revision,
            cache_dir=str(cache_dir),
            trust_remote_code=False,
            local_files_only=local_files_only,
        )
        if not callable(getattr(backend, "encode", None)) or not callable(
            getattr(backend, "apply_chat_template", None)
        ):
            raise TypeError("tokenizer lacks encode or apply_chat_template")
        return cls(cast(ChatTokenizer, backend))

    def count(self, text: str) -> int:
        return len(self.backend.encode(text, add_special_tokens=False))

    def count_messages(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolInfo] = ()
    ) -> int:
        conversation: list[dict[str, object]] = []
        for message in messages:
            if not isinstance(message.content, str):
                raise ValueError("the frozen text-only model requires string message content")
            row: dict[str, object] = {"role": message.role, "content": message.content}
            if isinstance(message, ChatMessageAssistant) and message.tool_calls:
                row["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function, "arguments": call.arguments},
                    }
                    for call in message.tool_calls
                ]
            if isinstance(message, ChatMessageTool):
                row["tool_call_id"] = message.tool_call_id
                if message.error:
                    row["content"] = f"Error: {message.error.message}"
            conversation.append(row)
        # Match Inspect's pinned vLLM/OpenAI-compatible wire definitions. The five
        # experiment tools use plain string parameters without extended constraints.
        definitions: list[dict[str, object]] = [
            {
                "type": "function",
                "function": {
                    "name": info.name,
                    "description": info.description,
                    "parameters": info.parameters.model_dump(exclude_none=True),
                    "strict": True,
                },
            }
            for info in tools
        ]
        return len(
            self.backend.apply_chat_template(
                conversation,
                tools=definitions or None,
                tokenize=True,
                add_generation_prompt=True,
            )
        )


def ensure_context_fits(
    tokenizer: Tokenizer,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolInfo],
    *,
    max_context_tokens: int,
    output_tokens: int,
) -> int:
    """Check the full served chat template, including tools and generation reserve."""
    if max_context_tokens <= 0 or output_tokens <= 0:
        raise ValueError("context and output budgets must be positive")
    tokens = tokenizer.count_messages(messages, tools)
    if tokens < 0:
        raise ValueError("token counts must be nonnegative")
    required = tokens + output_tokens
    if required > max_context_tokens:
        raise ContextOverflow(
            f"prompt plus output requires {required} tokens; window is {max_context_tokens}"
        )
    return tokens


def local_model(base_url: str, *, model_id: str = MODEL_ID) -> Model:
    """Create the no-auth loopback vLLM model without reading provider secrets."""
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("local model endpoint must be an unauthenticated HTTP loopback URL")
    # Connect directly to the existing vLLM OpenAI-compatible endpoint. Unlike
    # Inspect's server-launching vLLM adapter, this public provider forwards SDK
    # retry/timeout arguments, so one recorded attempt is one HTTP request.
    return get_model(
        f"openai-api/local/{model_id}",
        base_url=base_url,
        api_key="local-unused",
        max_retries=0,
        client_timeout=90,
        responses_api=False,
    )
