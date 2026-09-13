"""Portable records for reanalysis and Inspect exports; no custom web renderer."""

from typing import Self

from pydantic import Field, StrictStr, model_validator

from context_fidelity.contexts import ReportingContext
from context_fidelity.contracts import Arm, History, Record
from context_fidelity.score import Verdict, parse_report


class ReportView(Record):
    arm: Arm
    repetition: int = Field(ge=0)
    text: StrictStr
    verdict: Verdict | None = None

    @model_validator(mode="after")
    def validate_report_binding(self) -> Self:
        if self.verdict is None:
            return self
        try:
            parsed = parse_report(self.text)
        except ValueError:
            if not self.verdict.invalid_format or self.verdict.report is not None:
                raise ValueError("verdict does not match invalid raw output") from None
        else:
            if self.verdict.invalid_format or self.verdict.report != parsed:
                raise ValueError("verdict does not match parsed raw output")
        return self


class ReplayCase(Record):
    history: History
    contexts: tuple[ReportingContext, ...]
    reports: tuple[ReportView, ...] = ()

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        arms = [context.arm for context in self.contexts]
        keys = [(report.arm, report.repetition) for report in self.reports]
        if len(set(arms)) != len(arms) or len(set(keys)) != len(keys):
            raise ValueError("duplicate context or report")
        if any(context.history_id != self.history.history_id for context in self.contexts):
            raise ValueError("context history mismatch")
        if any(report.arm not in arms for report in self.reports):
            raise ValueError("report requires its exact context")
        return self
