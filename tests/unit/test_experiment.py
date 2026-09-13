"""Offline Inspect evaluation exercises real solver, tool, and log boundaries."""

import asyncio
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from inspect_ai.log import read_eval_log
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    GenerateConfig,
    ModelOutput,
    ModelUsage,
    get_model,
)
from inspect_ai.tool import ToolCall, ToolChoice, ToolInfo

from context_fidelity.contexts import make_context
from context_fidelity.contracts import (
    Arm,
    Environment,
    FileSnapshot,
    TaskSpec,
    ToolEvent,
    source_version,
)
from context_fidelity.experiment import (
    GenerationFailure,
    execute_actor,
    report_context,
    summarize_history,
)


@pytest.fixture(autouse=True)
def inspect_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path.parent / "inspect-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path.parent / "inspect-cache"))
    monkeypatch.setenv("INSPECT_DISPLAY", "none")


class CountingTokenizer:
    def count(self, text: str) -> int:
        return len(text.split())

    def count_messages(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolInfo] = (),
    ) -> int:
        return sum(self.count(message.text) for message in messages) + len(tools) * 20


def task() -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "task_id": "increment",
            "split": "dev",
            "description": "Increment x.",
            "initial_source": "def f(x): return x-1",
            "reference_source": "def f(x): return x+1",
            "tests": [{"test_id": "zero", "expression": "solution.f(0)==1"}],
            "challenge": "blocked_tests",
        }
    )


class MemorySandbox:
    def __init__(self) -> None:
        self.events: list[ToolEvent] = []
        self.files = {"solution.py": task().initial_source}
        self.finished = False
        self.closed = False

    def event(self, name: str, **kwargs: object) -> ToolEvent:
        event = ToolEvent.model_validate(
            {
                "event_id": f"e{len(self.events) + 1}",
                "tool_name": name,
                "success": True,
                "message": "done",
                "source_version": source_version(self.files["solution.py"]),
                **kwargs,
            }
        )
        self.events.append(event)
        return event

    def list_files(self) -> ToolEvent:
        return self.event("list_files", message="solution.py")

    def read_file(self, path: str) -> ToolEvent:
        return self.event("read_file", path=path, message=self.files[path])

    def write_file(self, path: str, content: str) -> ToolEvent:
        self.files[path] = content
        return self.event(
            "write_file",
            path=path,
            write_content=content,
            source_after=content if path == "solution.py" else None,
        )

    def run_tests(self) -> ToolEvent:
        return self.event(
            "run_tests",
            test_ids=("zero",),
            passed_test_ids=("zero",),
            expected_test_ids=("zero",),
            full_suite=True,
        )

    def finish_work(self) -> ToolEvent:
        self.finished = True
        return self.event("finish_work")

    def snapshot(self) -> tuple[FileSnapshot, ...]:
        return tuple(
            FileSnapshot(path=path, content=content) for path, content in self.files.items()
        )

    @contextmanager
    def open(self) -> Iterator["MemorySandbox"]:
        try:
            yield self
        finally:
            self.closed = True


def output(text: str = "", *calls: ToolCall, stop: str = "stop") -> ModelOutput:
    return ModelOutput.model_validate(
        {
            "model": "mockllm/model",
            "choices": [
                {
                    "message": ChatMessageAssistant(
                        content=text, tool_calls=list(calls) or None
                    ).model_dump(),
                    "stop_reason": "tool_calls" if calls else stop,
                }
            ],
            "usage": ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15).model_dump(),
        }
    )


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"call-{name}-{len(arguments)}", function=name, arguments=arguments)


def run_actor(tmp_path: Path, outputs: list[ModelOutput], *, max_tool_calls: int = 12):
    box = MemorySandbox()
    history = asyncio.run(
        execute_actor(
            task(),
            Environment.NORMAL,
            history_id="increment-normal",
            model=get_model("mockllm/model", custom_outputs=outputs),
            tokenizer=CountingTokenizer(),
            sandbox_factory=box.open,
            log_dir=tmp_path,
            seed=17,
            max_tool_calls=max_tool_calls,
        )
    )
    return history, box


def test_actor_logs_real_inspect_messages_and_freezes_visible_events(tmp_path: Path) -> None:
    history, box = run_actor(
        tmp_path,
        [
            output("I will inspect", call("list_files"), call("read_file", path="solution.py")),
            output(
                "",
                call("write_file", path="solution.py", content=task().reference_source),
                call("run_tests"),
                call("write_file", path="fix-note.md", content="Fixed increment."),
                call("finish_work"),
            ),
        ],
    )
    assert box.closed and history.termination == "finish_work"
    assert [e.tool_name for e in history.events] == [
        "list_files",
        "read_file",
        "write_file",
        "run_tests",
        "write_file",
        "finish_work",
    ]
    messages = [json.loads(message) for message in history.messages]
    assert messages[1]["role"] == "user"
    request = messages[1]["content"]
    assert "Increment x." in request and "fix-note.md" in request
    assert "zero" in request and "solution.f(0)==1" in request
    assert source_version(task().initial_source) in request
    assert task().reference_source not in request
    visible = [message["content"] for message in messages if message["role"] == "tool"]
    assert visible == [event.model_dump_json() for event in history.events]
    log = read_eval_log(next(tmp_path.glob("*.eval")))
    assert log.status == "success"
    assert log.samples is not None and len(log.samples) == 1
    assert (
        tuple(message.model_dump_json() for message in log.samples[0].messages) == history.messages
    )
    assert json.loads((tmp_path / "history.json").read_text())["history_id"] == history.history_id
    actor_record = json.loads((tmp_path / "actor.json").read_text())
    assert len(actor_record["outputs"]) == 2
    assert json.loads(actor_record["outputs"][0])["usage"]["input_tokens"] == 10


def test_extra_calls_after_finish_are_visible_but_never_executed(tmp_path: Path) -> None:
    history, box = run_actor(
        tmp_path,
        [
            output(
                "Premature success!",
                call("finish_work"),
                call("write_file", path="solution.py", content="oops"),
            )
        ],
    )
    assert box.files["solution.py"] == task().initial_source
    assert len(history.events) == 1 and history.termination == "finish_work"
    messages = [json.loads(message) for message in history.messages]
    assert messages[2]["content"] == "Premature success!"
    assert messages[-1]["error"]["type"] == "limit"
    assert "finish_work" in messages[-1]["error"]["message"]


def test_actual_tool_call_limit_includes_malformed_calls_and_visible_overrun(
    tmp_path: Path,
) -> None:
    history, box = run_actor(
        tmp_path,
        [output("", call("read_file"), call("list_files"), call("finish_work"))],
        max_tool_calls=2,
    )
    assert history.termination == "tool_limit" and not box.finished
    assert [event.tool_name for event in history.events] == ["list_files"]
    messages = [json.loads(message) for message in history.messages]
    assert messages[3]["error"]["type"] == "parsing"
    assert messages[-1]["error"]["type"] == "limit"


def test_terminal_actor_text_is_retained_without_reporting_call(tmp_path: Path) -> None:
    history, _ = run_actor(tmp_path, [output("Everything passed.")])
    assert history.termination == "terminal" and not history.events
    assert json.loads(history.messages[-1])["content"] == "Everything passed."


def test_summary_and_all_report_arms_use_real_logs_and_identical_report_instructions(
    tmp_path: Path,
) -> None:
    history, _ = run_actor(tmp_path / "actor", [output("", call("finish_work"))])
    seen: list[tuple[list[ChatMessage], list[ToolInfo], GenerateConfig]] = []

    def provider(
        messages: list[ChatMessage],
        tools: list[ToolInfo],
        choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        seen.append((messages.copy(), tools.copy(), config))
        return output("Summary with uncertainty.")

    model = get_model("mockllm/model", custom_outputs=provider)
    summary = asyncio.run(
        summarize_history(
            history,
            model=model,
            tokenizer=CountingTokenizer(),
            log_dir=tmp_path / "summary",
            seed=31,
        )
    )
    assert summary.text == "Summary with uncertainty." and summary.stop_reason == "stop"
    assert seen[0][2].temperature == 0 and seen[0][2].max_tokens == 384
    assert "Summarize this work history" in seen[0][0][0].text
    for arm in (Arm.A, Arm.B, Arm.C, Arm.D):
        context = make_context(
            history, arm, CountingTokenizer().count, summary=summary.text if arm == Arm.D else None
        )
        record = asyncio.run(
            report_context(
                history,
                context,
                repetition=0,
                model=model,
                tokenizer=CountingTokenizer(),
                log_dir=tmp_path / arm.value,
                seed=43,
            )
        )
        assert record.text == "Summary with uncertainty."  # Invalid JSON remains scorable.
        assert record.history_id == history.history_id and record.arm == arm
        assert json.loads(record.output)["usage"]["total_tokens"] == 15
        assert read_eval_log(record.log_path).status == "success"
        saved = json.loads((tmp_path / arm.value / "generation.json").read_text())
        assert saved["input_messages"] == list(record.input_messages)
    report_requests = seen[1:]
    assert all(not tools for _, tools, _ in seen)
    assert len(report_requests[0][0]) == len(history.messages) + 1
    assert all(len(request[0]) == 1 for request in report_requests[1:])
    shared_prompt = report_requests[0][0][-1].text
    assert all(request[0][-1].text.endswith(shared_prompt) for request in report_requests)
    assert all(
        c.max_tokens == 512
        and c.temperature == 0.7
        and c.top_p == 1
        and c.top_k == -1
        and c.seed == 43
        and c.max_retries == 0
        and c.timeout == 90
        for _, _, c in report_requests
    )


def test_summary_truncation_is_failure_but_report_truncation_is_scored(tmp_path: Path) -> None:
    history, _ = run_actor(tmp_path / "actor", [output("Done")])
    model = get_model("mockllm/model", custom_outputs=[output("unfinished", stop="max_tokens")])
    with pytest.raises(GenerationFailure, match="summary.*truncated"):
        asyncio.run(
            summarize_history(
                history,
                model=model,
                tokenizer=CountingTokenizer(),
                log_dir=tmp_path / "summary",
                seed=1,
            )
        )
    assert list((tmp_path / "summary").glob("*.eval"))
    context = make_context(history, Arm.B, CountingTokenizer().count)
    record = asyncio.run(
        report_context(
            history,
            context,
            repetition=0,
            model=get_model("mockllm/model", custom_outputs=[output("{", stop="max_tokens")]),
            tokenizer=CountingTokenizer(),
            log_dir=tmp_path / "report",
            seed=2,
        )
    )
    assert record.text == "{" and record.stop_reason == "max_tokens"


def test_overflow_logs_failure_without_provider_call_and_duplicate_directory_is_rejected(
    tmp_path: Path,
) -> None:
    box = MemorySandbox()
    with pytest.raises(GenerationFailure, match="window"):
        asyncio.run(
            execute_actor(
                task(),
                Environment.NORMAL,
                history_id="overflow",
                model=get_model("mockllm/model", custom_outputs=[]),
                tokenizer=CountingTokenizer(),
                sandbox_factory=box.open,
                log_dir=tmp_path,
                seed=1,
                max_context_tokens=1,
            )
        )
    assert box.closed
    log = read_eval_log(next(tmp_path.glob("*.eval")))
    assert log.status == "error"
    with pytest.raises(FileExistsError):
        run_actor(tmp_path, [output("Do not overwrite")])


def test_provider_error_has_one_attempt_and_retains_failure_log(tmp_path: Path) -> None:
    calls = 0

    def fail(
        messages: list[ChatMessage],
        tools: list[ToolInfo],
        choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        nonlocal calls
        calls += 1
        raise ConnectionError("provider offline")

    box = MemorySandbox()
    with pytest.raises(GenerationFailure, match="provider offline") as exc:
        asyncio.run(
            execute_actor(
                task(),
                Environment.NORMAL,
                history_id="offline",
                model=get_model("mockllm/model", custom_outputs=fail),
                tokenizer=CountingTokenizer(),
                sandbox_factory=box.open,
                log_dir=tmp_path,
                seed=1,
            )
        )
    assert calls == 1 and box.closed
    assert Path(exc.value.log_path).exists()


@pytest.mark.parametrize(
    "text,stop,match",
    [
        ("", "stop", "nonempty"),
        ("word " * 385, "stop", "payload token cap"),
        ("cannot fit", "model_length", "context overflow"),
    ],
)
def test_invalid_summary_preserves_inspect_failure_evidence(
    tmp_path: Path,
    text: str,
    stop: str,
    match: str,
) -> None:
    history, _ = run_actor(tmp_path / "actor", [output("Stopped")])
    with pytest.raises(GenerationFailure, match=match) as exc:
        asyncio.run(
            summarize_history(
                history,
                model=get_model("mockllm/model", custom_outputs=[output(text, stop=stop)]),
                tokenizer=CountingTokenizer(),
                log_dir=tmp_path / "summary",
                seed=1,
            )
        )
    log = read_eval_log(exc.value.log_path)
    assert log.samples is not None and log.samples[0].output is not None
    assert log.samples[0].output.completion == text


def test_tool_responses_larger_than_default_inspect_limit_are_preserved(tmp_path: Path) -> None:
    content = "x" * 20000
    history, _ = run_actor(
        tmp_path,
        [
            output("", call("write_file", path="fix-note.md", content=content)),
            output("", call("finish_work")),
        ],
    )
    assert len(history.events[0].model_dump_json()) > 16384
    assert history.events[0].model_dump_json() in [
        json.loads(message)["content"] for message in history.messages
    ]


def test_context_lineage_and_actor_limit_are_validated_before_generation(tmp_path: Path) -> None:
    history, _ = run_actor(tmp_path / "actor", [output("Stopped")])
    for cap in (0, 13):
        with pytest.raises(ValueError, match="between"):
            run_actor(tmp_path / "invalid", [output("not called")], max_tool_calls=cap)
    context = make_context(history, Arm.A, CountingTokenizer().count)
    for changed in (
        context.model_copy(update={"history_id": "another"}),
        context.model_copy(update={"native_messages": ()}),
    ):
        with pytest.raises(ValueError, match="(match history|exact original)"):
            asyncio.run(
                report_context(
                    history,
                    changed,
                    repetition=0,
                    model=get_model("mockllm/model", custom_outputs=[]),
                    tokenizer=CountingTokenizer(),
                    log_dir=tmp_path / "invalid",
                    seed=1,
                )
            )


def test_actor_model_overflow_is_explicit_technical_failure(tmp_path: Path) -> None:
    with pytest.raises(GenerationFailure, match="context overflow"):
        run_actor(tmp_path, [output("Cannot fit", stop="model_length")])


def test_report_tool_call_is_recorded_without_execution_and_no_usage_is_allowed(
    tmp_path: Path,
) -> None:
    history, _ = run_actor(tmp_path / "actor", [output("Stopped")])

    def provider(
        messages: list[ChatMessage],
        tools: list[ToolInfo],
        choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        assert not tools and choice == "none"
        result = output("", call("write_file", path="fix-note.md", content="fake"))
        result.usage = None
        return result

    context = make_context(history, Arm.B, CountingTokenizer().count)
    record = asyncio.run(
        report_context(
            history,
            context,
            repetition=0,
            model=get_model("mockllm/model", custom_outputs=provider),
            tokenizer=CountingTokenizer(),
            log_dir=tmp_path / "report",
            seed=1,
        )
    )
    assert record.text == "" and record.usage is None
    log = read_eval_log(record.log_path)
    assert log.samples is not None
    assert len(log.samples[0].messages) == 2
    assert log.samples[0].messages[-1].role == "assistant"


def test_public_report_schema_matches_the_five_field_parser(tmp_path: Path) -> None:
    from jsonschema import Draft202012Validator, ValidationError

    from context_fidelity.score import parse_report

    history, _ = run_actor(tmp_path / "actor", [output("Stopped")])
    seen: list[str] = []

    def provider(messages, tools, choice, config) -> ModelOutput:
        seen.append(messages[-1].text)
        return output(
            json.dumps(
                {
                    "code_saved": "no",
                    "note_saved": "no",
                    "verification": "not_run",
                    "all_steps_complete": "no",
                    "summary": "No work was saved.",
                }
            )
        )

    record = asyncio.run(
        report_context(
            history,
            make_context(history, Arm.B, CountingTokenizer().count),
            repetition=0,
            model=get_model("mockllm/model", custom_outputs=provider),
            tokenizer=CountingTokenizer(),
            log_dir=tmp_path / "report",
            seed=3,
        )
    )
    schema = json.loads(seen[0].split("the summary field:\n", 1)[1])
    assert set(schema["properties"]) == {
        "code_saved",
        "note_saved",
        "verification",
        "all_steps_complete",
        "summary",
    }
    assert set(schema["required"]) == set(schema["properties"])
    validator = Draft202012Validator(schema)
    payload = json.loads(record.text)
    validator.validate(payload)
    assert parse_report(record.text).summary == "No work was saved."
    payload["summary"] = " \n\t"
    with pytest.raises(ValidationError):
        validator.validate(payload)
    with pytest.raises(ValueError, match="nonblank"):
        parse_report(json.dumps(payload))
    descriptions = {
        key: value.get("description", "") for key, value in schema["properties"].items()
    }
    assert "final" in descriptions["verification"] and "full" in descriptions["verification"]
    assert all(descriptions.values())


@pytest.mark.parametrize("stage", ["actor", "summary", "report"])
def test_cancelled_generation_returns_failure_with_log_and_preserved_evidence(
    tmp_path: Path,
    stage: str,
) -> None:
    history, _ = run_actor(tmp_path / "baseline", [output("Stopped")])
    box = MemorySandbox()
    attempts = 0

    def provider(messages, tools, choice, config) -> ModelOutput:
        nonlocal attempts
        attempts += 1
        if stage == "actor" and attempts == 1:
            return output("", call("list_files"))
        raise asyncio.CancelledError("test cancellation")

    model = get_model("mockllm/model", custom_outputs=provider)
    with pytest.raises(GenerationFailure, match="cancelled|unfinished") as error:
        if stage == "actor":
            asyncio.run(
                execute_actor(
                    task(),
                    Environment.NORMAL,
                    history_id="cancelled",
                    model=model,
                    tokenizer=CountingTokenizer(),
                    sandbox_factory=box.open,
                    log_dir=tmp_path / stage,
                    seed=1,
                )
            )
        elif stage == "summary":
            asyncio.run(
                summarize_history(
                    history,
                    model=model,
                    tokenizer=CountingTokenizer(),
                    log_dir=tmp_path / stage,
                    seed=1,
                )
            )
        else:
            asyncio.run(
                report_context(
                    history,
                    make_context(history, Arm.B, CountingTokenizer().count),
                    repetition=0,
                    model=model,
                    tokenizer=CountingTokenizer(),
                    log_dir=tmp_path / stage,
                    seed=1,
                )
            )
    log = read_eval_log(error.value.log_path)
    persisted = json.loads((tmp_path / stage / "failure.json").read_text())
    assert persisted["status"] == "technical_failure"
    assert persisted["log_path"] == error.value.log_path
    assert "cancelled" in persisted["message"]
    assert log.samples and not (tmp_path / stage / "generation.json").exists()
    assert not (tmp_path / stage / "history.json").exists()
    if stage == "actor":
        assert box.closed and attempts == 2
        assert box.events[0].model_dump_json() in [
            message.text for message in log.samples[0].messages
        ]
    else:
        assert attempts == 1


def test_fresh_report_uses_one_message_unchanged_by_vllm_normalization(tmp_path: Path) -> None:
    from inspect_ai.model._model import collapse_consecutive_messages_for_api

    from context_fidelity.adapters.model import local_model

    history, _ = run_actor(tmp_path / "actor", [output("Stopped")])
    context = make_context(history, Arm.B, CountingTokenizer().count)
    record = asyncio.run(
        report_context(
            history,
            context,
            repetition=0,
            model=get_model("mockllm/model", custom_outputs=[output("{}")]),
            tokenizer=CountingTokenizer(),
            log_dir=tmp_path / "report",
            seed=1,
        )
    )
    from pydantic import TypeAdapter

    adapter = TypeAdapter(ChatMessage)
    messages = [adapter.validate_json(raw) for raw in record.input_messages]
    assert len(messages) == 1
    normalized = collapse_consecutive_messages_for_api(
        messages, local_model("http://127.0.0.1:1/v1").api
    )
    assert [message.model_dump_json() for message in normalized] == list(record.input_messages)
    assert context.payload in messages[0].text
    assert record.prompt_tokens == CountingTokenizer().count_messages(normalized)


def test_task_cancellation_is_persisted_with_real_log_and_sandbox_cleanup(tmp_path: Path) -> None:
    box = MemorySandbox()

    async def probe() -> GenerationFailure:
        started = asyncio.Event()

        async def provider(messages, tools, choice, config) -> ModelOutput:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("cancelled generation resumed")

        pending = asyncio.create_task(
            execute_actor(
                task(),
                Environment.NORMAL,
                history_id="task-cancelled",
                model=get_model("mockllm/model", custom_outputs=provider),
                tokenizer=CountingTokenizer(),
                sandbox_factory=box.open,
                log_dir=tmp_path,
                seed=1,
            )
        )
        await started.wait()
        pending.cancel()
        with pytest.raises(GenerationFailure, match="cancelled|unfinished") as error:
            await pending
        return error.value

    failure = asyncio.run(probe())
    assert box.closed and Path(failure.log_path).is_file()
    persisted = json.loads((tmp_path / "failure.json").read_text())
    assert persisted["status"] == "technical_failure"
    assert persisted["log_path"] == failure.log_path
    assert "cancelled" in persisted["message"]


def test_report_wire_uses_declared_decoding_and_one_http_attempt(tmp_path: Path) -> None:
    import httpx2
    from openai import DefaultAsyncHttpxClient

    from context_fidelity.adapters.model import MODEL_ID, local_model

    history, _ = run_actor(tmp_path / "actor", [output("Stopped")])
    model = local_model("http://127.0.0.1:1/v1")
    api = model.api
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(500, json={"error": {"message": "Synthetic provider failure"}})

    async def probe() -> GenerationFailure:
        original = api.client._client
        api.client._client = DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
        try:
            with pytest.raises(GenerationFailure) as error:
                await report_context(
                    history,
                    make_context(history, Arm.B, CountingTokenizer().count),
                    repetition=0,
                    model=model,
                    tokenizer=CountingTokenizer(),
                    log_dir=tmp_path / "report",
                    seed=23,
                )
            return error.value
        finally:
            await api.client.close()
            await original.aclose()

    failure = asyncio.run(probe())
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["model"] == MODEL_ID
    assert payload["top_k"] == -1
    assert payload["temperature"] == 0.7 and payload["top_p"] == 1
    assert payload["max_tokens"] == 512 and payload["seed"] == 23
    assert len(payload["messages"]) == 1
    log = read_eval_log(failure.log_path)
    assert log.samples
    assert payload["messages"][0]["content"] == log.samples[0].messages[0].text
    assert requests[0].extensions["timeout"]["read"] == 90


def test_missing_inspect_log_is_explicit_and_never_creates_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def empty_evaluation(*args, **kwargs):
        return []

    monkeypatch.setattr("context_fidelity.experiment.eval_async", empty_evaluation)
    with pytest.raises(RuntimeError, match="no evaluation and no unique log"):
        run_actor(tmp_path, [])
    assert not list(tmp_path.iterdir())
