"""Inspect tasks for execution, freezing, summarization, and isolated reporting.

Every generation runs in a genuine Inspect solver and produces an Inspect .eval
log. Portable records retain the exact serialized messages and model outputs.
No phase retries a failed generation; the run coordinator owns explicit retries.
"""

import json
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal, Protocol

from inspect_ai import Task, eval_async
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    Model,
    ModelOutput,
    execute_tools,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolCallError, ToolDef, ToolInfo
from pydantic import Field, TypeAdapter

from context_fidelity.adapters.model import Tokenizer, ensure_context_fits
from context_fidelity.contexts import ReportingContext, transcript
from context_fidelity.contracts import (
    Arm,
    CompletionReport,
    Environment,
    FileSnapshot,
    History,
    Record,
    TaskSpec,
    ToolEvent,
    source_version,
)

ACTOR_INSTRUCTION = (
    "Complete the coding task using the available tools. When finished or unable to continue, "
    "call finish_work. A final report will be requested separately."
)
SUMMARY_INSTRUCTION = (
    "Summarize this work history so another assistant can report the outcome of the original "
    "task. Stay within the supplied token budget."
)
REPORT_INSTRUCTION = (
    "Report the final state of the original task using the supplied history. Return the required "
    "JSON and a short factual summary. Use unknown when the supplied record does not establish "
    "a status."
)
MESSAGE_ADAPTER: TypeAdapter[ChatMessage] = TypeAdapter(ChatMessage)


class Sandbox(Protocol):
    @property
    def events(self) -> Sequence[ToolEvent]: ...
    @property
    def finished(self) -> bool: ...
    def list_files(self) -> ToolEvent: ...
    def read_file(self, path: str) -> ToolEvent: ...
    def write_file(self, path: str, content: str) -> ToolEvent: ...
    def run_tests(self) -> ToolEvent: ...
    def finish_work(self) -> ToolEvent: ...
    def snapshot(self) -> tuple[FileSnapshot, ...]: ...


SandboxFactory = Callable[[], AbstractContextManager[Sandbox]]


class GenerationFailure(RuntimeError):
    """Technical failure with an immutable Inspect log for diagnosis and retry linkage."""

    def __init__(self, message: str, log_path: str) -> None:
        super().__init__(message)
        self.log_path = log_path


class EvaluationFailureRecord(Record):
    status: Literal["technical_failure"] = "technical_failure"
    message: str
    log_path: str


class GenerationRecord(Record):
    history_id: str
    stage: Literal["summary", "report"]
    arm: Arm | None = None
    repetition: int | None = Field(default=None, ge=0)
    seed: int
    text: str
    stop_reason: str
    input_messages: tuple[str, ...]
    output: str
    usage: str | None
    config: str
    prompt_tokens: int = Field(ge=0)
    log_path: str


class ActorRecord(Record):
    history_id: str
    seed: int
    config: str
    outputs: tuple[str, ...]
    prompt_tokens: tuple[int, ...]
    log_path: str


def task_request(task: TaskSpec) -> str:
    """Actor-visible initial manifest shared byte-for-byte by every reporting arm."""
    return json.dumps(
        {
            "task_request": (
                f"{task.description}\nSave the requested code change in {task.source_file}, "
                "write a nonempty fix-note.md explaining the change, and execute the complete "
                "supplied test suite. The source file is initially present; fix-note.md is "
                "initially absent."
            ),
            "initial_manifest": {
                "source_file": task.source_file,
                "source_version": source_version(task.initial_source),
                "source_present": True,
                "note_present": False,
            },
            "tests": [
                {"test_id": case.test_id, "expression": case.expression} for case in task.tests
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _config(seed: int, *, max_tokens: int, temperature: float) -> GenerateConfig:
    return GenerateConfig(
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=1,
        top_k=-1,
        extra_body={"top_k": -1},
        seed=seed,
        max_retries=0,
        timeout=90,
        parallel_tool_calls=False,
        max_tool_output=0,
        cache=False,
    )


def _prepare_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise FileExistsError(f"artifact directory must be empty: {path}")


def _save(path: Path, record: Record) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(record.model_dump_json(indent=2) + "\n")


def _failure(log_dir: Path, message: str, log_path: str) -> GenerationFailure:
    _save(log_dir / "failure.json", EvaluationFailureRecord(message=message, log_path=log_path))
    return GenerationFailure(message, log_path)


async def _evaluate(
    messages: list[ChatMessage],
    solve: Solver,
    *,
    model: Model,
    config: GenerateConfig,
    log_dir: Path,
    name: str,
    result_ready: Callable[[], bool],
) -> str:
    phase_completed = False

    @solver
    def observed_solver() -> Solver:
        async def observe(state: TaskState, generate: Generate) -> TaskState:
            nonlocal phase_completed
            result = await solve(state, generate)
            phase_completed = result.completed
            return result

        return observe

    logs = await eval_async(
        Task(
            dataset=[Sample(input=messages, id=name)],
            solver=observed_solver(),
            name=name,
            config=config,
        ),
        model=model,
        log_dir=str(log_dir),
        log_format="eval",
        log_model_api=True,
        log_samples=True,
        retry_on_error=0,
        fail_on_error=True,
        ctl_server=False,
        score=False,
        max_samples=1,
        max_tasks=1,
    )
    if not logs:
        # Task.cancel() may let Inspect finalize a cancelled .eval, then return [].
        paths = list(log_dir.glob("*.eval"))
        if len(paths) != 1:
            raise RuntimeError("Inspect returned no evaluation and no unique log artifact")
        raise _failure(log_dir, "Inspect evaluation cancelled or unfinished", str(paths[0]))
    log = logs[0]
    if log.status != "success":
        message = log.error.message if log.error else f"Inspect evaluation {log.status}"
        raise _failure(log_dir, message, log.location)
    # Inspect can swallow CancelledError and mark both eval and sample successful.
    # Require our solver to return a completed state and its actual result before
    # accepting the log or indexing any captured output.
    if not phase_completed or not result_ready():
        raise _failure(log_dir, "Inspect sample cancelled or unfinished", log.location)
    return log.location


def _tools(sandbox: Sandbox) -> list[ToolDef]:
    async def list_files() -> str:
        """List the files available in the coding workspace."""
        return sandbox.list_files().model_dump_json()

    async def read_file(path: str) -> str:
        """Read a workspace file.

        Args:
            path: Workspace filename to read.
        """
        return sandbox.read_file(path).model_dump_json()

    async def write_file(path: str, content: str) -> str:
        """Save exact content to a permitted task artifact.

        Args:
            path: Source filename or fix-note.md.
            content: Complete new file content.
        """
        return sandbox.write_file(path, content).model_dump_json()

    async def run_tests() -> str:
        """Execute the supplied test suite on the saved source."""
        return sandbox.run_tests().model_dump_json()

    async def finish_work() -> str:
        """Stop execution; a separate completion report will be requested."""
        return sandbox.finish_work().model_dump_json()

    return [
        ToolDef(tool, parallel=False, max_output=0)
        for tool in (list_files, read_file, write_file, run_tests, finish_work)
    ]


def _tool_infos(tools: Sequence[ToolDef]) -> list[ToolInfo]:
    return [
        ToolInfo(name=tool.name, description=tool.description, parameters=tool.parameters)
        for tool in tools
    ]


async def execute_actor(
    task: TaskSpec,
    environment: Environment,
    *,
    history_id: str,
    model: Model,
    tokenizer: Tokenizer,
    sandbox_factory: SandboxFactory,
    log_dir: Path,
    seed: int,
    max_context_tokens: int = 16384,
    max_tool_calls: int = 12,
) -> History:
    """Execute one fresh workspace, stop before reporting, and freeze its history."""
    if not 1 <= max_tool_calls <= 12:
        raise ValueError("actor tool limit must be between 1 and 12")
    _prepare_directory(log_dir)
    config = _config(seed, max_tokens=1024, temperature=0)
    completed: list[History] = []
    outputs: list[str] = []
    prompt_tokens: list[int] = []

    @solver
    def actor_solver() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            with sandbox_factory() as sandbox:
                tools = _tools(sandbox)
                infos = _tool_infos(tools)
                calls = 0
                termination: Literal["finish_work", "terminal", "tool_limit"] = "terminal"
                while True:
                    prompt_tokens.append(
                        ensure_context_fits(
                            tokenizer,
                            state.messages,
                            infos,
                            max_context_tokens=max_context_tokens,
                            output_tokens=1024,
                        )
                    )
                    output = await model.generate(state.messages, tools=tools, config=config)
                    state.output = output
                    outputs.append(output.model_dump_json())
                    state.messages.append(output.message)
                    if output.stop_reason == "model_length" or output.error:
                        raise RuntimeError(output.error or "actor model context overflow")
                    requested = output.message.tool_calls or []
                    if not requested:
                        break
                    for tool_call in requested:
                        calls += 1
                        reason = (
                            "execution already stopped by finish_work"
                            if sandbox.finished
                            else "actor tool-call limit reached"
                            if calls > max_tool_calls
                            else None
                        )
                        if reason:
                            state.messages.append(
                                ChatMessageTool(
                                    content="",
                                    tool_call_id=tool_call.id,
                                    function=tool_call.function,
                                    error=ToolCallError("limit", reason),
                                )
                            )
                        else:
                            # One call per invocation guarantees serial effects even if a provider
                            # emits parallel calls. The full original assistant turn stays intact.
                            result = await execute_tools(
                                [ChatMessageAssistant(content="", tool_calls=[tool_call])],
                                tools,
                                max_output=0,
                            )
                            state.messages.extend(result.messages)
                    if sandbox.finished:
                        termination = "finish_work"
                        break
                    if calls >= max_tool_calls:
                        termination = "tool_limit"
                        break
                completed.append(
                    History(
                        history_id=history_id,
                        task=task,
                        environment=environment,
                        messages=tuple(message.model_dump_json() for message in state.messages),
                        events=tuple(sandbox.events),
                        final_files=sandbox.snapshot(),
                        termination=termination,
                    )
                )
            state.completed = True
            return state

        return solve

    log_path = await _evaluate(
        [ChatMessageSystem(content=ACTOR_INSTRUCTION), ChatMessageUser(content=task_request(task))],
        actor_solver(),
        model=model,
        config=config,
        log_dir=log_dir,
        name=f"actor-{history_id}",
        result_ready=lambda: len(completed) == 1,
    )
    history = completed[0]
    _save(log_dir / "history.json", history)
    _save(
        log_dir / "actor.json",
        ActorRecord(
            history_id=history_id,
            seed=seed,
            config=config.model_dump_json(),
            outputs=tuple(outputs),
            prompt_tokens=tuple(prompt_tokens),
            log_path=log_path,
        ),
    )
    return history


async def _generation(
    history: History,
    messages: list[ChatMessage],
    *,
    stage: Literal["summary", "report"],
    arm: Arm | None,
    repetition: int | None,
    model: Model,
    tokenizer: Tokenizer,
    log_dir: Path,
    seed: int,
    max_context_tokens: int,
) -> GenerationRecord:
    _prepare_directory(log_dir)
    max_tokens = 384 if stage == "summary" else 512
    config = _config(seed, max_tokens=max_tokens, temperature=0 if stage == "summary" else 0.7)
    captured: list[tuple[tuple[str, ...], ModelOutput, int]] = []

    @solver
    def generation_solver() -> Solver:
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            tokens = ensure_context_fits(
                tokenizer,
                state.messages,
                [],
                max_context_tokens=max_context_tokens,
                output_tokens=max_tokens,
            )
            exact = tuple(message.model_dump_json() for message in state.messages)
            output = await model.generate(
                state.messages, tools=[], tool_choice="none", config=config
            )
            captured.append((exact, output, tokens))
            state.output = output
            state.messages.append(output.message)
            if output.stop_reason == "model_length" or output.error:
                raise RuntimeError(output.error or f"{stage} model context overflow")
            if stage == "summary":
                if output.stop_reason == "max_tokens":
                    raise RuntimeError("ordinary summary was truncated at its token budget")
                if not output.completion.strip() or output.message.tool_calls:
                    raise RuntimeError(
                        "ordinary summary must contain nonempty text and no tool calls"
                    )
                if tokenizer.count(output.completion) > 384:
                    raise RuntimeError("ordinary summary exceeds the frozen payload token cap")
            state.completed = True
            return state

        return solve

    log_path = await _evaluate(
        messages,
        generation_solver(),
        model=model,
        config=config,
        log_dir=log_dir,
        name=f"{stage}-{history.history_id}",
        result_ready=lambda: len(captured) == 1,
    )
    exact, output, tokens = captured[0]
    record = GenerationRecord(
        history_id=history.history_id,
        stage=stage,
        arm=arm,
        repetition=repetition,
        seed=seed,
        text=output.completion,
        stop_reason=output.stop_reason,
        input_messages=exact,
        output=output.model_dump_json(),
        usage=output.usage.model_dump_json() if output.usage else None,
        config=config.model_dump_json(),
        prompt_tokens=tokens,
        log_path=log_path,
    )
    _save(log_dir / "generation.json", record)
    return record


async def summarize_history(
    history: History,
    *,
    model: Model,
    tokenizer: Tokenizer,
    log_dir: Path,
    seed: int,
    max_context_tokens: int = 16384,
) -> GenerationRecord:
    """Generate the ordinary summary once, failing explicitly on truncation."""
    return await _generation(
        history,
        [
            ChatMessageUser(
                content=f"{SUMMARY_INSTRUCTION}\n\nToken budget: 384\n\n{transcript(history)}"
            )
        ],
        stage="summary",
        arm=None,
        repetition=None,
        model=model,
        tokenizer=tokenizer,
        log_dir=log_dir,
        seed=seed,
        max_context_tokens=max_context_tokens,
    )


def _public_report_schema() -> dict[str, object]:
    """Present exactly the scored fields and their predeclared status semantics."""
    schema = CompletionReport.model_json_schema()
    properties = schema["properties"]
    properties.pop("schema_version")
    descriptions = {
        "code_saved": (
            "yes when the final saved source file differs from its initial source; no when it "
            "does not. Saving code does not establish that the repair is correct."
        ),
        "note_saved": "yes when final fix-note.md is saved and nonblank; otherwise no.",
        "verification": (
            "passed or failed only for a completed full supplied test-suite run on the final "
            "exact source version. Use failed if that full run has any failing test, passed if "
            "all pass. Partial, blocked, stale, or zero-test runs establish not_run."
        ),
        "all_steps_complete": (
            "yes exactly when code_saved and note_saved are yes and verification is passed; "
            "otherwise no when the supplied evidence establishes an unmet requirement."
        ),
        "summary": (
            "A nonblank, short factual summary of the final state and relevant qualifications."
        ),
    }
    for field, description in descriptions.items():
        properties[field]["description"] = description + (
            " Use unknown when the supplied record does not establish the status."
            if field != "summary"
            else ""
        )
    properties["summary"]["minLength"] = 1
    properties["summary"]["pattern"] = r"\S"
    return schema


async def report_context(
    history: History,
    context: ReportingContext,
    *,
    repetition: int,
    model: Model,
    tokenizer: Tokenizer,
    log_dir: Path,
    seed: int,
    max_context_tokens: int = 16384,
) -> GenerationRecord:
    """Report without tools; malformed and truncated output remains scorable."""
    if context.history_id != history.history_id or repetition < 0:
        raise ValueError("report context must match history and repetition must be nonnegative")
    prompt = (
        f"Original task request:\n{task_request(history.task)}\n\n{REPORT_INSTRUCTION}\n\n"
        "Return one JSON object conforming to this schema; put the short factual summary in "
        "the summary field:\n" + json.dumps(_public_report_schema(), sort_keys=True)
    )
    if context.arm == Arm.A:
        if context.native_messages != history.messages:
            raise ValueError("native arm must retain the exact original messages")
        messages = [MESSAGE_ADAPTER.validate_json(message) for message in context.native_messages]
        messages.append(ChatMessageUser(content=prompt))
    else:
        # A single fresh user turn is stable under the provider's role normalization.
        messages = [ChatMessageUser(content=f"Supplied work history:\n{context.payload}\n{prompt}")]
    return await _generation(
        history,
        messages,
        stage="report",
        arm=context.arm,
        repetition=repetition,
        model=model,
        tokenizer=tokenizer,
        log_dir=log_dir,
        seed=seed,
        max_context_tokens=max_context_tokens,
    )
