"""Offline producer-to-results fixtures preserve the frozen matrix and evidence."""

import hashlib
import json
from pathlib import Path

import pytest
from inspect_ai._eval.task.util import sample_messages
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    ModelOutput,
)
from inspect_ai.tool import ToolCall

from context_fidelity.artifacts import freeze_files, save_json
from context_fidelity.contexts import make_context
from context_fidelity.contracts import (
    Arm,
    FileSnapshot,
    History,
    TaskSpec,
    ToolEvent,
    source_version,
)
from context_fidelity.evidence import derive_truth
from context_fidelity.experiment import MESSAGE_ADAPTER, GenerationRecord, task_request
from context_fidelity.pipeline import PhaseResult, StageFailure, SummaryAuditManifest, prepare_study
from context_fidelity.results import load_results, write_results
from context_fidelity.score import ContextSupport, ProseReview, context_digest

ROOT = Path(__file__).resolve().parents[2]
STAMP = "2026-09-12T12:00:00Z"
RAW = json.dumps(
    {
        "code_saved": "no",
        "note_saved": "no",
        "verification": "not_run",
        "all_steps_complete": "no",
        "summary": "No repair or note was saved, and no full suite ran.",
    }
)


def fixture(
    tmp_path: Path,
    *,
    failure: bool = False,
    actor_failure: bool = False,
    tool_exchange: bool = False,
) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    for name in (
        "docs/project-spec.md",
        "docs/review-rubric.md",
        "docs/experiments/ED-001-context-comparison.md",
        "docs/experiments/ED-002-evidence-restoration.md",
        "uv.lock",
        "src/context_fidelity/original.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Original frozen input.\n")
    (root / "config.yaml").write_bytes((ROOT / "config.yaml").read_bytes())
    tasks = []
    for index in range(4):
        task = TaskSpec.model_validate(
            {
                "task_id": f"task-{index}",
                "split": "dev",
                "description": "Return 42.",
                "initial_source": "def f(): return 0",
                "reference_source": "def f(): return 42",
                "tests": [{"test_id": "value", "expression": "f() == 42"}],
                "challenge": "blocked_tests",
            }
        )
        tasks.append(task)
        save_json(root / "tasks/dev" / f"{task.task_id}.json", task)
    run = root / "runs/offline"
    plan = prepare_study(
        root,
        config_path=root / "config.yaml",
        task_dir=root / "tasks/dev",
        run_dir=run,
        run_id="offline",
        split="dev",
        created_at=STAMP,
    )
    by_task = {task.task_id: task for task in tasks}
    histories = {}
    contexts = {}
    collect_failures = []
    for index, planned in enumerate(plan.histories):
        folder = run / "histories" / planned.history_id
        if actor_failure and index == 0:
            error = StageFailure(
                history_id=planned.history_id,
                stage="actor",
                error="sandbox unavailable",
                log_path="actor-failed.eval",
                retry_policy="unclassified_no_retry",
            )
            save_json(folder / "actor-failure.json", error)
            collect_failures.append(error)
            continue
        task = by_task[planned.task_id]
        messages = (ChatMessageUser(content=task_request(task)).model_dump_json(),)
        events = ()
        if tool_exchange:
            event = ToolEvent(
                event_id="e1",
                tool_name="read_file",
                path=task.source_file,
                success=True,
                message=task.initial_source,
                source_version=source_version(task.initial_source),
            )
            events = (event,)
            messages += (
                ChatMessageAssistant(
                    content="I will inspect the source.",
                    source="generate",
                    model=plan.config.model_id,
                    metadata={"step": 1},
                    tool_calls=[
                        ToolCall(
                            id="call-1", function="read_file", arguments={"path": task.source_file}
                        )
                    ],
                ).model_dump_json(),
                ChatMessageTool(
                    content=event.model_dump_json(),
                    source=None,
                    tool_call_id="call-1",
                    function="read_file",
                ).model_dump_json(),
            )
        history = History(
            history_id=planned.history_id,
            task=task,
            environment=planned.environment,
            messages=messages,
            events=events,
            final_files=(FileSnapshot(path=task.source_file, content=task.initial_source),),
            termination="terminal",
        )
        histories[planned.history_id] = history
        save_json(folder / "history.json", history)
        save_json(folder / "truth.json", derive_truth(history))
        for arm in (Arm.A, Arm.B, Arm.C, Arm.D):
            supplied = make_context(
                history,
                arm,
                lambda text: len(text.split()),
                summary="No repair or note was saved; no full suite ran; work is incomplete."
                if arm == Arm.D
                else None,
            )
            contexts[(planned.history_id, arm)] = supplied
            save_json(folder / "contexts" / f"{arm}.json", supplied)
        summary = generation(
            history,
            contexts[(planned.history_id, Arm.D)],
            planned.summary_seed,
            None,
            stage="summary",
            text=contexts[(planned.history_id, Arm.D)].payload,
            folder=folder / "summary/attempt-1",
            config=plan.config,
        )
        save_json(folder / "summary.json", summary)
    collected = PhaseResult(
        phase="collect", completed=len(histories), failures=tuple(collect_failures)
    )
    save_json(run / "collect.json", collected)
    save_json(
        run / "collection-freeze.json",
        freeze_files(
            run,
            [run / "collect.json", *[p for p in (run / "histories").rglob("*") if p.is_file()]],
            created_at=STAMP,
        ),
    )
    save_json(
        run / "report-started.json",
        {
            "created_at": STAMP,
            "report_order": [r.model_dump(mode="json") for r in plan.reports],
            "summary_audit": None,
        },
    )
    failures = []
    completed = 0
    failed_report = next(
        (report for report in plan.reports if report.history_id in histories), None
    )
    for planned in plan.reports:
        name = f"{planned.history_id}-{planned.arm}-{planned.repetition}"
        if planned.history_id not in histories or (failure and planned == failed_report):
            error = StageFailure(
                history_id=planned.history_id,
                stage="report",
                arm=planned.arm,
                repetition=planned.repetition,
                error="planned history unavailable"
                if planned.history_id not in histories
                else "generation failed",
                log_path="failed.eval",
                retry_policy="missing_input"
                if planned.history_id not in histories
                else "unclassified_no_retry",
            )
            failures.append(error)
            save_json(run / "report-failures" / f"{name}.json", error)
            continue
        folder = (
            run
            / "histories"
            / planned.history_id
            / f"report-{planned.arm}-{planned.repetition}/attempt-1"
        )
        record = generation(
            histories[planned.history_id],
            contexts[(planned.history_id, planned.arm)],
            planned.seed,
            planned.repetition,
            stage="report",
            text=RAW,
            folder=folder,
            config=plan.config,
        )
        save_json(run / "reports" / f"{name}.json", record)
        completed += 1
    save_json(
        run / "report.json",
        PhaseResult(phase="report", completed=completed, failures=tuple(failures)),
    )
    refresh_report_freeze(run)
    return root, run


def generation(history, context, seed, repetition, *, stage, text, folder, config):
    prompt = (
        f"Original task request:\n{task_request(history.task)}\n\nFrozen reporting instruction."
    )
    messages = (
        (*history.messages, ChatMessageUser(content=prompt).model_dump_json())
        if context.arm == Arm.A
        else (
            ChatMessageUser(
                content=f"Supplied work history:\n{context.payload}\n{prompt}"
            ).model_dump_json(),
        )
    )
    # Exercise Inspect's real input preparation, including source attribution reset.
    messages = tuple(
        json.dumps(message.model_dump(mode="json"), sort_keys=True, indent=2)
        for message in sample_messages(
            Sample(input=[MESSAGE_ADAPTER.validate_json(raw) for raw in messages])
        )
    )
    output = ModelOutput.from_content(config.model_id, text)
    record = GenerationRecord(
        history_id=history.history_id,
        stage=stage,
        arm=context.arm if stage == "report" else None,
        repetition=repetition,
        seed=seed,
        text=text,
        stop_reason="stop",
        input_messages=messages,
        output=output.model_dump_json(),
        usage=None,
        config=GenerateConfig(
            seed=seed,
            max_tokens=config.report_output_tokens
            if stage == "report"
            else config.summary_payload_cap,
            temperature=config.report_temperature
            if stage == "report"
            else config.summary_temperature,
            top_p=config.top_p,
            top_k=config.top_k,
            extra_body={"top_k": config.top_k},
        ).model_dump_json(),
        prompt_tokens=10,
        log_path=str(folder / "inspect.eval"),
    )
    save_json(folder / "generation.json", record)
    (folder / "inspect.eval").write_bytes(b"Offline fixture log; no model was called.")
    return record


def refresh_report_freeze(run: Path) -> None:
    path = run / "report-freeze.json"
    if path.exists():
        path.unlink()
    inputs = [
        run / "report.json",
        run / "report-started.json",
        *list((run / "reports").glob("*.json")),
        *list((run / "report-failures").glob("*.json")),
        *[p for p in (run / "histories").glob("*/report-*/attempt-1/*") if p.is_file()],
    ]
    if (run / "summary-audit.json").exists():
        inputs.append(run / "summary-audit.json")
    save_json(path, freeze_files(run, inputs, created_at=STAMP))


def load(root, run, **kwargs):
    return load_results(root, run, analysis_id="analysis-001", created_at=STAMP, **kwargs)


def audits(bundle, kind="human"):
    return SummaryAuditManifest(
        created_at=STAMP,
        supports=tuple(
            ContextSupport(
                history_id=case.history.history_id,
                context_digest=context_digest(next(c for c in case.contexts if c.arm == Arm.D)),
                code_saved="no",
                note_saved="no",
                verification="not_run",
                all_steps_complete="no",
                reviewer_id="reviewer-1",
                reviewer_kind=kind,
                notes="The summary explicitly establishes these statuses.",
            )
            for case in bundle.cases
            if any(c.arm == Arm.D for c in case.contexts)
        ),
    )


def test_completed_raw_reports_remain_pending_without_inferred_summary_audits(
    tmp_path: Path,
) -> None:
    root, run = fixture(tmp_path)
    bundle = load(root, run)
    assert len(bundle.cells) == 32 and len(bundle.planned_pairs) == 8
    assert len(bundle.cases) == 8 and len(bundle.blinded.items) == 32
    counts = {row.arm: row for row in bundle.arm_counts}
    assert (counts[Arm.C].completed, counts[Arm.C].scored, counts[Arm.C].primary_pending) == (
        8,
        8,
        8,
    )
    assert (counts[Arm.D].completed, counts[Arm.D].scored, counts[Arm.D].support_audit_pending) == (
        8,
        0,
        8,
    )
    for cell in bundle.cells:
        assert cell.generation is not None and cell.failure is None
        assert (cell.verdict is None) == (cell.arm == Arm.D)
    effect = next(
        row.estimate
        for row in bundle.effects
        if row.metric == "coverage" and row.comparison == "C-D"
    )
    assert effect.missing_right == 8 and effect.n_complete_clusters == 0
    assert (effect.missing_lower, effect.missing_upper) == (0, 1)


def test_native_input_accepts_inspect_source_reset_without_changing_frozen_history(
    tmp_path: Path,
) -> None:
    root, run = fixture(tmp_path, tool_exchange=True)
    frozen = (run / "collection-freeze.json").read_bytes()
    bundle = load(root, run)
    native = next(cell for cell in bundle.cells if cell.arm == Arm.A)
    assert native.generation is not None
    case = next(case for case in bundle.cases if case.history.history_id == native.key.history_id)
    assert [json.loads(raw)["source"] for raw in case.history.messages] == [None, "generate", None]
    assert all(json.loads(raw)["source"] == "input" for raw in native.generation.input_messages)
    assert len(bundle.cells) == 32
    assert (run / "collection-freeze.json").read_bytes() == frozen


@pytest.mark.parametrize(
    "field", ["content", "role", "id", "metadata", "model", "tool_calls", "tool_call_id", "source"]
)
def test_native_source_reset_does_not_hide_changed_message_evidence(
    tmp_path: Path, field: str
) -> None:
    root, run = fixture(tmp_path, tool_exchange=True)
    path = next((run / "reports").glob("*-A-0.json"))
    data = json.loads(path.read_text())
    index = 2 if field == "tool_call_id" else 1
    message = json.loads(data["input_messages"][index])
    message[field] = (
        {"step": 2} if field == "metadata" else [] if field == "tool_calls" else "changed"
    )
    data["input_messages"][index] = json.dumps(message)
    path.write_text(json.dumps(data))
    retained = run / "histories" / data["history_id"] / "report-A-0/attempt-1/generation.json"
    retained.write_text(json.dumps(data))
    refresh_report_freeze(run)
    with pytest.raises(ValueError, match="native generation input"):
        load(root, run)


def test_explicit_audits_and_human_reviews_close_primary_without_dropping_cells(
    tmp_path: Path,
) -> None:
    root, run = fixture(tmp_path)
    preliminary = load(root, run)
    support = audits(preliminary)
    reviews = tuple(
        ProseReview(
            history_id=key.history_id,
            context_digest=key.context_digest,
            report_digest=key.report_digest,
            false_claim=False,
            unsupported_claim=False,
            reviewer_id="reviewer-1",
            reviewer_kind="human",
            blinded=True,
            unblinding_note="",
        )
        for key in preliminary.blinded.key
    )
    bundle = load(root, run, summary_audit=support, prose_reviews=reviews)
    assert all(
        cell.verdict is not None and cell.verdict.unreliable is False for cell in bundle.cells
    )
    assert all(row.review_complete == 8 and row.primary_reliable == 8 for row in bundle.arm_counts)
    assert len(bundle.effects) == 6
    assert all(
        row.estimate.effect == 0 and row.estimate.n_complete_clusters == 4 for row in bundle.effects
    )


def test_source_changes_do_not_rewrite_or_invalidate_original_frozen_run(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    original = (run / "freeze.json").read_bytes()
    (root / "src/context_fidelity/original.py").write_text("Changed analysis-era source.\n")
    bundle = load(root, run)
    assert bundle.origin_freeze.files and bundle.analysis_code.files
    assert (run / "freeze.json").read_bytes() == original
    assert len(bundle.cells) == 32


def test_failures_keep_all_planned_cells_and_per_arm_missing_counts(tmp_path: Path) -> None:
    root, run = fixture(tmp_path, failure=True, actor_failure=True)
    bundle = load(root, run)
    missing = [cell for cell in bundle.cells if cell.failure is not None]
    assert len(bundle.cells) == 32
    assert len(missing) == 5
    assert sum(row.technical_missing for row in bundle.arm_counts) == len(missing)
    assert len(bundle.cases) == 7
    assert len(bundle.blinded.items) == 32 - len(missing)


def test_write_results_is_exclusive_and_saves_blind_key_separately(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    bundle = load(root, run)
    destination = tmp_path / "analysis-001"
    write_results(bundle, destination)
    assert (destination / "analysis-input.json").is_file()
    assert (destination / "analysis-freeze.json").is_file()
    items = json.loads((destination / "blinded-items.json").read_text())
    key = json.loads((destination / "blinded-key.json").read_text())
    assert len(items) == len(key) == 32
    assert all("arm" not in item and "history_id" not in item for item in items)
    assert all("arm" in item and "history_id" in item for item in key)
    with pytest.raises(FileExistsError):
        write_results(bundle, destination)


def replace_json(path: Path, **changes) -> None:
    data = json.loads(path.read_text())
    path.write_text(json.dumps(data | changes))


def refreeze(root: Path, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        entry["sha256"] = hashlib.sha256((root / entry["path"]).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))


@pytest.mark.parametrize("target", ["plan", "collection", "report"])
def test_changed_frozen_bytes_are_rejected_before_scoring(tmp_path: Path, target: str) -> None:
    root, run = fixture(tmp_path)
    path = (
        run / "plan.json"
        if target == "plan"
        else next(
            (run / ("reports" if target == "report" else "histories")).glob(
                "*.json" if target == "report" else "*/history.json"
            )
        )
    )
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        load(root, run)


@pytest.mark.parametrize(
    "mode", ["unlisted_report", "unlisted_context", "duplicate_report", "conflicting_outcome"]
)
def test_late_unplanned_or_conflicting_artifacts_cannot_change_frozen_missingness(
    tmp_path: Path, mode: str
) -> None:
    root, run = fixture(tmp_path)
    source = next((run / "reports").glob("*.json"))
    if mode == "unlisted_context":
        existing = next((run / "histories").glob("*/contexts/C.json"))
        existing.with_name("rescue.json").write_bytes(existing.read_bytes())
    elif mode == "conflicting_outcome":
        raw = json.loads(source.read_text())
        failure = StageFailure(
            history_id=raw["history_id"],
            stage="report",
            arm=raw["arm"],
            repetition=raw["repetition"],
            error="contradictory side",
            log_path="fail.eval",
            retry_policy="unclassified_no_retry",
        )
        save_json(run / "report-failures" / source.name, failure)
        refresh_report_freeze(run)
    else:
        source.with_name("extra.json").write_bytes(source.read_bytes())
        if mode == "duplicate_report":
            refresh_report_freeze(run)
    with pytest.raises(ValueError, match="unplanned|unfrozen|exactly one"):
        load(root, run)


@pytest.mark.parametrize("mode", ["lineage", "retained", "output", "config", "prompt", "log"])
def test_incoherent_generation_records_fail_even_with_matching_file_hashes(
    tmp_path: Path, mode: str
) -> None:
    root, run = fixture(tmp_path)
    path = next((run / "reports").glob("*-B-0.json"))
    data = json.loads(path.read_text())
    inner = run / "histories" / data["history_id"] / "report-B-0/attempt-1/generation.json"
    if mode == "lineage":
        data["seed"] += 1
    elif mode == "retained":
        data["text"] = "changed completion"
    elif mode == "output":
        output = json.loads(data["output"])
        output["completion"] = "different output"
        data["output"] = json.dumps(output)
    elif mode == "config":
        config = json.loads(data["config"])
        config["temperature"] = 0.9
        data["config"] = json.dumps(config)
    elif mode == "prompt":
        data["input_messages"] = [ChatMessageUser(content="Different context.").model_dump_json()]
    elif mode == "log":
        data["log_path"] = "not-retained.eval"
    path.write_text(json.dumps(data))
    if mode != "retained":
        inner.write_text(json.dumps(data))
    refresh_report_freeze(run)
    with pytest.raises(ValueError, match="lineage|retained|output|configuration|input|log"):
        load(root, run)


@pytest.mark.parametrize(
    "mode",
    ["history", "truth", "context", "summary", "collect_total", "report_total", "report_order"],
)
def test_saved_phase_metadata_must_agree_with_evidence_and_plan(tmp_path: Path, mode: str) -> None:
    root, run = fixture(tmp_path)
    folder = next((run / "histories").iterdir())
    if mode == "history":
        replace_json(folder / "history.json", history_id="different")
    elif mode == "truth":
        replace_json(folder / "truth.json", code_saved="yes")
    elif mode == "context":
        replace_json(folder / "contexts/C.json", arm="B")
    elif mode == "summary":
        replace_json(folder / "summary.json", text="A different summary.")
    elif mode == "collect_total":
        replace_json(run / "collect.json", completed=7)
    elif mode == "report_total":
        replace_json(run / "report.json", completed=31)
    else:
        replace_json(run / "report-started.json", report_order=[])
    refreeze(run, run / "collection-freeze.json")
    refreeze(run, run / "report-freeze.json")
    with pytest.raises(ValueError, match="lineage|truth|summary|totals|order"):
        load(root, run)


def test_missing_report_freeze_fails_closed(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    (run / "report-freeze.json").unlink()
    with pytest.raises(FileNotFoundError):
        load(root, run)


def test_audits_and_reviews_must_match_completed_frozen_contexts(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    initial = load(root, run)
    support = audits(initial)
    for invalid in (
        support.model_copy(update={"supports": support.supports * 2}),
        support.model_copy(update={"supports": support.supports[:-1]}),
    ):
        with pytest.raises(ValueError, match="summary audit"):
            load(root, run, summary_audit=invalid)
    first = initial.blinded.key[0]
    review = ProseReview(
        history_id=first.history_id,
        context_digest=first.context_digest,
        report_digest=first.report_digest,
        false_claim=False,
        unsupported_claim=False,
        reviewer_id="reviewer",
        reviewer_kind="human",
        blinded=True,
        unblinding_note="",
    )
    with pytest.raises(ValueError, match="duplicate prose"):
        load(root, run, prose_reviews=(review, review))
    with pytest.raises(ValueError, match="does not match"):
        load(root, run, prose_reviews=(review.model_copy(update={"report_digest": "0" * 64}),))


def test_pre_report_audit_is_used_by_default_and_bound_to_started_record(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    audit = audits(load(root, run), kind="assistant")
    save_json(run / "summary-audit.json", audit)
    replace_json(run / "report-started.json", summary_audit=audit.model_dump(mode="json"))
    refresh_report_freeze(run)
    bundle = load(root, run)
    assert all(row.scored == 8 and row.primary_pending == 8 for row in bundle.arm_counts)
    assert bundle.origin_summary_audit == bundle.summary_audit == audit
    replace_json(run / "report-started.json", summary_audit=None)
    refreeze(run, run / "report-freeze.json")
    with pytest.raises(ValueError, match="pre-report summary audit"):
        load(root, run)


def test_invalid_summary_report_is_scored_without_inventing_a_support_audit(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    path = next((run / "reports").glob("*-D-0.json"))
    data = json.loads(path.read_text())
    data["text"] = "not JSON"
    data["output"] = ModelOutput.from_content(
        json.loads(data["output"])["model"], "not JSON"
    ).model_dump_json()
    path.write_text(json.dumps(data))
    (run / "histories" / data["history_id"] / "report-D-0/attempt-1/generation.json").write_text(
        json.dumps(data)
    )
    refresh_report_freeze(run)
    bundle = load(root, run)
    counts = next(row for row in bundle.arm_counts if row.arm == Arm.D)
    assert (counts.completed, counts.scored, counts.invalid_format, counts.primary_unreliable) == (
        8,
        1,
        1,
        1,
    )
    assert counts.support_audit_pending == 7
    assert counts.review_pending == 8


def test_results_directory_contains_only_json_and_outside_root_is_rejected(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    bundle = load(root, run)
    destination = tmp_path / "numeric-only"
    write_results(bundle, destination)
    assert all(path.suffix == ".json" for path in destination.iterdir())
    with pytest.raises(ValueError, match="inside"):
        load(root / "somewhere-else", run)


@pytest.mark.parametrize("mode", ["unbound_plan", "missing_frozen_log", "changed_matrix"])
def test_plan_membership_and_immutable_inputs_are_required(tmp_path: Path, mode: str) -> None:
    root, run = fixture(tmp_path)
    if mode == "unbound_plan":
        manifest = json.loads((run / "freeze.json").read_text())
        manifest["files"] = [
            entry for entry in manifest["files"] if not entry["path"].endswith("/plan.json")
        ]
        (run / "freeze.json").write_text(json.dumps(manifest))
    elif mode == "missing_frozen_log":
        next((run / "histories").glob("*/report-*/attempt-1/inspect.eval")).unlink()
    else:
        plan = json.loads((run / "plan.json").read_text())
        plan["reports"] = plan["reports"][:-1]
        (run / "plan.json").write_text(json.dumps(plan))
        refreeze(root, run / "freeze.json")
    with pytest.raises(ValueError, match="bound|missing|matrix"):
        load(root, run)


@pytest.mark.parametrize(
    "message",
    [
        {"role": "assistant", "content": "Finished."},
        {"role": "user", "content": "Plain prose."},
        {"role": "user", "content": "{}"},
        {"role": "user", "content": None},
    ],
)
def test_original_task_request_cannot_be_replaced_by_incidental_messages(
    tmp_path: Path, message: dict
) -> None:
    root, run = fixture(tmp_path)
    path = next((run / "histories").glob("*/history.json"))
    replace_json(path, messages=[json.dumps(message)])
    refreeze(run, run / "collection-freeze.json")
    with pytest.raises(ValueError, match="original task request"):
        load(root, run)


def omit_context(run: Path, arm: Arm, *, keep_reports: bool = False) -> None:
    context = next((run / "histories").glob(f"*/contexts/{arm}.json"))
    history_id = context.parent.parent.name
    context.unlink()
    name = "summary-failure.json" if arm == Arm.D else f"context-{arm}-failure.json"
    failure = StageFailure(
        history_id=history_id,
        stage="summary" if arm == Arm.D else "context",
        arm=arm,
        error="payload could not fit",
        log_path="context-failed.eval",
        retry_policy="deterministic_no_retry",
    )
    save_json(context.parent.parent / name, failure)
    collected = json.loads((run / "collect.json").read_text())
    collected["failures"].append(failure.model_dump(mode="json"))
    (run / "collect.json").write_text(json.dumps(collected))
    old = json.loads((run / "collection-freeze.json").read_text())
    paths = [run / entry["path"] for entry in old["files"] if (run / entry["path"]).exists()]
    paths.append(context.parent.parent / name)
    (run / "collection-freeze.json").unlink()
    save_json(run / "collection-freeze.json", freeze_files(run, paths, created_at=STAMP))
    if not keep_reports:
        reports = json.loads((run / "report.json").read_text())
        for path in (run / "reports").glob(f"{history_id}-{arm}-*.json"):
            data = json.loads(path.read_text())
            failure = StageFailure(
                history_id=history_id,
                stage="report",
                arm=arm,
                repetition=data["repetition"],
                error="context unavailable",
                log_path="context-failed.eval",
                retry_policy="missing_input",
            )
            save_json(run / "report-failures" / path.name, failure)
            reports["failures"].append(failure.model_dump(mode="json"))
            reports["completed"] -= 1
            path.unlink()
            for retained in (
                run / "histories" / history_id / f"report-{arm}-{data['repetition']}/attempt-1"
            ).iterdir():
                retained.unlink()
        (run / "report.json").write_text(json.dumps(reports))
    refresh_report_freeze(run)


@pytest.mark.parametrize("arm", [Arm.C, Arm.D])
def test_frozen_context_failures_remain_missing_cells(tmp_path: Path, arm: Arm) -> None:
    root, run = fixture(tmp_path)
    omit_context(run, arm)
    result = load(root, run)
    assert len(result.cells) == 32
    assert next(count for count in result.arm_counts if count.arm == arm).technical_missing == 1
    assert len(result.blinded.items) == 31


def test_completed_report_cannot_refer_to_a_failed_context(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    omit_context(run, Arm.D, keep_reports=True)
    with pytest.raises(ValueError, match="no frozen"):
        load(root, run)


@pytest.mark.parametrize("mode", ["extra_top_k", "usage", "unplanned_generation"])
def test_full_generation_configuration_and_retained_attempt_inventory_are_validated(
    tmp_path: Path, mode: str
) -> None:
    root, run = fixture(tmp_path)
    path = next((run / "reports").glob("*-B-0.json"))
    data = json.loads(path.read_text())
    inner = run / "histories" / data["history_id"] / "report-B-0/attempt-1/generation.json"
    if mode == "unplanned_generation":
        extra = inner.parents[2] / "report-B-9/attempt-1/generation.json"
        extra.parent.mkdir(parents=True)
        extra.write_bytes(inner.read_bytes())
    else:
        if mode == "extra_top_k":
            config = json.loads(data["config"])
            config["extra_body"] = {"top_k": 50}
            data["config"] = json.dumps(config)
        else:
            data["usage"] = '{"input_tokens":999,"output_tokens":10,"total_tokens":1009}'
        path.write_text(json.dumps(data))
        inner.write_text(json.dumps(data))
        refresh_report_freeze(run)
    with pytest.raises(ValueError, match="configuration|usage|unplanned"):
        load(root, run)


def test_completed_reports_retain_missing_human_review_counts(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    bundle = load(root, run)
    assert all(count.review_pending == 8 for count in bundle.arm_counts)


def test_pending_summary_reports_still_revalidate_review_provenance(tmp_path: Path) -> None:
    root, run = fixture(tmp_path)
    bundle = load(root, run)
    key = next(key for key in bundle.blinded.key if key.arm == Arm.D)
    review = ProseReview(
        history_id=key.history_id,
        context_digest=key.context_digest,
        report_digest=key.report_digest,
        false_claim=False,
        unsupported_claim=False,
        reviewer_id="reviewer",
        reviewer_kind="human",
        blinded=True,
        unblinding_note="",
    )
    with pytest.raises(ValueError):
        load(root, run, prose_reviews=(review.model_copy(update={"false_claim": "no"}),))
