"""Reporting-context strategies with explicit, lossless budget failures.

The compact strategy reads visible tool metadata only. It never consults the
final-file oracle or inserts completion labels. Every write/test event remains
in chronological order; an over-budget history is an explicit failure.
"""

from collections.abc import Callable

from pydantic import Field

from context_fidelity.contracts import Arm, History, Record, source_version

TokenCounter = Callable[[str], int]


class BudgetExceeded(ValueError):
    """The complete requested payload cannot fit the predeclared budget."""


class ReportingContext(Record):
    history_id: str
    arm: Arm
    payload: str
    token_count: int = Field(ge=0)
    native_messages: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()


def transcript(history: History) -> str:
    """Preserve every byte of each serialized message and its role label."""
    return "\n".join(history.messages)


def compact_evidence(history: History) -> tuple[str, tuple[str, ...]]:
    """Extract complete write/test chronology using source-content aliases.

    ``v0`` is the initial source disclosed in the original request. Other labels
    stand only for exact SHA256 equality, not assessments of code correctness.
    Write lengths are deterministic properties of actor-visible write contents.
    """
    versions = {source_version(history.task.initial_source): "v0"}
    rows = [
        "Complete write/test record; only reads/list/finish omitted. "
        "Initially source present, note absent. "
        "v0=initial source; version labels denote exact source-content equality."
    ]
    ids: list[str] = []
    for event in history.events:
        if event.tool_name not in {"write_file", "run_tests"}:
            continue
        version = versions.setdefault(event.source_version, f"v{len(versions)}")
        ids.append(event.event_id)
        status = "ok" if event.success else "error"
        if event.tool_name == "write_file":
            row = f"{event.event_id} write {event.path} {version} {status}"
            if event.success:
                assert event.write_content is not None  # Validated successful-write contract.
                row += (
                    f" bytes={len(event.write_content.encode('utf-8'))}"
                    f" nonblank={str(bool(event.write_content.strip())).lower()}"
                )
        else:
            row = (
                f"{event.event_id} tests {version} {status}"
                f" expected={','.join(event.expected_test_ids) or '-'}"
                f" collected={','.join(event.test_ids) or '-'}"
                f" passed={','.join(event.passed_test_ids) or '-'}"
                f" failed={','.join(event.failed_test_ids) or '-'}"
                f" full={str(event.full_suite).lower()}"
            )
        rows.append(row)
    return "\n".join(rows), tuple(ids)


def _bounded(payload: str, count: TokenCounter, cap: int) -> int:
    if cap <= 0:
        raise ValueError("token cap must be positive")
    tokens = count(payload)
    if tokens < 0:
        raise ValueError("token count must be nonnegative")
    if tokens > cap:
        raise BudgetExceeded(f"payload requires {tokens} tokens; frozen cap is {cap}")
    return tokens


def make_context(
    history: History,
    arm: Arm,
    count: TokenCounter,
    *,
    cap: int = 384,
    summary: str | None = None,
) -> ReportingContext:
    """Build one primary arm; summary generation belongs to the model boundary."""
    if arm != Arm.D and summary is not None:
        raise ValueError("only arm D accepts a summary")
    native: tuple[str, ...] = ()
    ids: tuple[str, ...] = ()
    if arm in {Arm.A, Arm.B}:
        payload = transcript(history)
        tokens = count(payload)
        if arm == Arm.A:
            native = history.messages
    elif arm == Arm.C:
        payload, ids = compact_evidence(history)
        tokens = _bounded(payload, count, cap)
    elif arm == Arm.D:
        if summary is None or not summary.strip():
            raise ValueError("ordinary summary must be nonempty")
        payload = summary
        tokens = _bounded(payload, count, cap)
    else:
        raise ValueError("use restoration_contexts for diagnostic arms")
    return ReportingContext(
        history_id=history.history_id,
        arm=arm,
        payload=payload,
        token_count=tokens,
        native_messages=native,
        event_ids=ids,
    )


def restoration_contexts(
    ordinary: ReportingContext,
    decisive: str,
    control: str,
    count: TokenCounter,
    *,
    decisive_id: str,
    control_id: str,
) -> tuple[ReportingContext, ReportingContext]:
    """Construct matched additions after the blinded omission audit selects them.

    This validates lengths and lineage; it cannot validate semantic eligibility.
    The frozen audit must establish omission, decisiveness, and control validity.
    """
    if ordinary.arm != Arm.D:
        raise ValueError("restoration must start from an ordinary summary")
    if not decisive.strip() or not control.strip():
        raise ValueError("additions must be nonempty")
    decisive_tokens = _bounded(decisive, count, 128)
    control_tokens = _bounded(control, count, 128)
    if abs(decisive_tokens - control_tokens) > 8:
        raise ValueError("addition lengths must be within eight tokens")
    records: list[ReportingContext] = []
    for arm, addition, event_id in (
        (Arm.RESCUE, decisive, decisive_id),
        (Arm.CONTROL, control, control_id),
    ):
        payload = ordinary.payload + "\n\nAdditional tool event:\n" + addition
        records.append(
            ReportingContext(
                history_id=ordinary.history_id,
                arm=arm,
                payload=payload,
                token_count=_bounded(payload, count, 512),
                event_ids=(event_id,),
            )
        )
    return records[0], records[1]
