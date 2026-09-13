"""An offline replay of actual histories, contexts, reports, and review state."""

import re
from html import escape
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, StrictStr, model_validator

from context_fidelity.contexts import ReportingContext
from context_fidelity.contracts import Arm, CompletionReport, History, Record
from context_fidelity.evidence import derive_truth
from context_fidelity.score import STATUS_FIELDS, Verdict, parse_report

ARM_NAMES = {
    Arm.A: "Native history",
    Arm.B: "Full external record",
    Arm.C: "Compact evidence",
    Arm.D: "Ordinary summary",
}


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


_STYLE = """
:root{
color-scheme:light;
--ink:#19332c;
--muted:#63756d;
--paper:#f5f5ee;
--line:#d9dfd4;
--green:#147653;
--red:#a6412b;
--amber:#906213}

*{
box-sizing:border-box}
body{
margin:0;
background:var(--paper);
color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}

button,select{
font:inherit;
color:inherit}
button,select,summary{
cursor:pointer}
header,main,footer{
max-width:1440px;
margin:auto;
padding:28px 40px}

header{
border-bottom:1px solid var(--line);
display:flex;
align-items:center;
justify-content:space-between;
padding-top:22px;
padding-bottom:22px}

.brand{
font-weight:750;
letter-spacing:-.5px;
font-size:20px}
.monogram{
display:inline-grid;
place-items:center;
background:var(--ink);
color:#fff;
width:34px;
height:34px;
border-radius:8px;
margin-right:10px;
font-size:13px}

.eyebrow,.label{
font:11px/1.4 ui-monospace,SFMono-Regular,monospace;
letter-spacing:1.5px;
text-transform:uppercase;
color:var(--muted)}

.hero{
display:flex;
justify-content:space-between;
align-items:end;
gap:30px;
padding:18px 0 28px}
h1{
font-size:clamp(30px,3.6vw,52px);
letter-spacing:-2px;
font-weight:580;
line-height:1.12;
margin:10px 0 12px}
h2{
font-size:22px;
letter-spacing:-.6px;
margin:0 0 8px}
h3{
font-size:16px;
margin:0 0 10px}
p{
margin:8px 0;
color:var(--muted)}

.lede{
max-width:700px;
font-size:16px}
.stats{
display:flex;
gap:30px;
white-space:nowrap}
.stat b{
display:block;
font-size:29px;
font-weight:550}
.stat span{
font-size:12px;
color:var(--muted)}

.toolbar{
display:flex;
align-items:center;
justify-content:space-between;
gap:16px;
border-top:1px solid var(--line);
border-bottom:1px solid var(--line);
padding:18px 0;
margin-bottom:24px}
select{
border:1px solid var(--line);
border-radius:7px;
background:#fff;
padding:9px 34px 9px 12px;
max-width:100%}

.pill{
display:inline-block;
padding:4px 9px;
border:1px solid var(--line);
border-radius:30px;
font:11px/1.5 ui-monospace,monospace;
color:var(--muted);
white-space:nowrap}

.case-heading{
display:flex;
justify-content:space-between;
gap:20px}
.task-text{
max-width:1000px}
.truth{
display:flex;
gap:8px;
flex-wrap:wrap;
padding:16px 0 20px}
.truth .pill{
background:#fff}
.comparison{
display:grid;
grid-template-columns:1fr 1fr;
gap:18px;
margin-bottom:20px}

.comparison-slot,.evidence,.study{
border:1px solid var(--line);
border-radius:11px;
background:#fff;
overflow:hidden}
.slot-head{
padding:16px 20px;
display:flex;
align-items:center;
justify-content:space-between;
gap:12px;
background:#fafbf6;
border-bottom:1px solid var(--line)}
.slot-head select{
max-width:260px}
.context-content{
padding:18px 20px}
.payload{
height:280px;
overflow:auto;
background:#f7f8f3;
border:1px solid #e6e9e0;
border-radius:7px;
padding:14px;
font-size:12px;
line-height:1.65;
scrollbar-width:thin}

pre{
white-space:pre-wrap;
overflow-wrap:anywhere;
font-family:ui-monospace,SFMono-Regular,Consolas,monospace;
font-size:12px;
margin:10px 0}
code{
font-family:ui-monospace,SFMono-Regular,monospace}
.context-meta{
display:flex;
justify-content:space-between;
align-items:center;
margin-bottom:10px}
.reports{
padding:0 20px 20px}
.report{
border-top:1px solid var(--line);
padding-top:15px;
margin-top:8px}
.fields{
display:grid;
grid-template-columns:1fr 1fr;
gap:7px;
margin:12px 0}
.field{
border:1px solid var(--line);
border-radius:6px;
padding:9px 11px}
.field span{
display:block;
font-size:11px;
color:var(--muted)}
.field b{
font-size:14px;
font-weight:550}
.good b{
color:var(--green)}
.bad b{
color:var(--red)}
.unknown b{
color:var(--amber)}

.summary-text{
color:var(--ink);
font-size:14px}
.review-note,.empty{
font-size:12px;
color:var(--muted)}
.empty{
padding:24px}
.evidence{
margin-top:18px;
padding:20px}
.events{
padding:0;
list-style:none;
margin:16px 0 0}
.event{
border-top:1px solid var(--line);
padding:12px 0}
.event summary{
display:flex;
align-items:center;
gap:12px;
flex-wrap:wrap}
.event-id{
font:11px ui-monospace,monospace;
color:var(--muted);
min-width:68px}
.event-msg{
color:var(--muted);
font-size:12px}
.event pre{
max-height:320px;
overflow:auto;
background:#f7f8f3;
padding:12px;
border-radius:6px}

.study{
margin-top:28px;
padding:24px}
.study img{
display:block;
width:100%;
max-width:1100px;
height:auto;
margin:20px auto}
.note{
background:#edf3e9;
border-left:3px solid var(--green);
padding:12px 16px;
font-size:13px;
color:var(--ink);
margin:12px 0 20px}
.note.pending{
background:#faf4e6;
border-left-color:var(--amber)}
footer{
font-size:12px;
color:var(--muted);
border-top:1px solid var(--line);
margin-top:20px;
padding-top:18px;
padding-bottom:22px}

[hidden]{
display:none!important}
@media(max-width:800px){
header,main,footer{
padding-left:20px;
padding-right:20px}
.comparison{
grid-template-columns:1fr}
.hero{
display:block}
.stats{
margin-top:20px}
.toolbar{
align-items:start;
flex-direction:column}
.case-heading{
display:block}
.payload{
height:230px}
.slot-head{
padding:14px}
.context-content,.reports{
padding-left:14px;
padding-right:14px}
}
@media print{
.case[hidden]{
display:block!important}
.payload{
height:auto}
.comparison{
break-inside:avoid}
select{
border:0}
.study img{
max-height:500px;
object-fit:contain}
}

"""

_SCRIPT = """
const choice=document.querySelector('#case-choice');
choice.addEventListener('change',()=>{
  document.querySelectorAll('.case').forEach(section=>{
    section.hidden=section.dataset.case!==choice.value;
  });
});
document.querySelectorAll('.comparison-slot').forEach(slot=>{
  const select=slot.querySelector('.arm-choice');
  const show=()=>slot.querySelectorAll('.arm-content').forEach(panel=>{
    panel.hidden=panel.dataset.arm!==select.value;
  });
  select.addEventListener('change',show);show();
});
document.querySelectorAll('.repeat-choice').forEach(select=>{
  const show=()=>select.closest('.reports').querySelectorAll('.report').forEach(report=>{
    report.hidden=report.dataset.repeat!==select.value;
  });
  select.addEventListener('change',show);show();
});
"""


def _fields(report: CompletionReport, history: History) -> str:
    truth = derive_truth(history)
    fields = []
    for name in STATUS_FIELDS:
        value = str(getattr(report, name))
        status = (
            "unknown" if value == "unknown" else "good" if value == getattr(truth, name) else "bad"
        )
        fields.append(
            f'<div class="field {status}"><span>{escape(name.replace("_", " "))}</span>'
            f"<b>{escape(value)}</b></div>"
        )
    return '<div class="fields">' + "".join(fields) + "</div>"


def _report(view: ReportView, history: History) -> str:
    try:
        parsed = parse_report(view.text)
    except ValueError:
        body = '<p class="note pending">Invalid required JSON output</p>'
    else:
        body = _fields(parsed, history) + f'<p class="summary-text">{escape(parsed.summary)}</p>'
    verdict = view.verdict
    review = "Human prose review pending"
    if verdict is not None and verdict.review_complete:
        review = "Human review complete · " + (
            "unreliable report" if verdict.unreliable else "supported report"
        )
    elif verdict is not None and verdict.structured_unreliable:
        review = "Structured discrepancy detected · Human prose review pending"
    return (
        f'<div class="report" data-repeat="{view.repetition}">{body}'
        f'<p class="review-note">{escape(review)}</p>'
        '<details><summary class="review-note">Exact model output</summary>'
        f"<pre>{escape(view.text)}</pre></details></div>"
    )


def _arm(case: ReplayCase, arm: Arm) -> str:
    context = next((value for value in case.contexts if value.arm == arm), None)
    if context is None:
        return (
            f'<div class="arm-content" data-arm="{arm}"><p class="empty">'
            "Context unavailable; inspect the run failure record.</p></div>"
        )
    reports = sorted(
        (value for value in case.reports if value.arm == arm), key=lambda value: value.repetition
    )
    options = "".join(
        f'<option value="{value.repetition}">Report {value.repetition + 1}</option>'
        for value in reports
    )
    body = (
        "".join(_report(value, case.history) for value in reports)
        if reports
        else '<p class="empty">Reporting has not run for this context.</p>'
    )
    return (
        f'<div class="arm-content" data-arm="{arm}"><div class="context-content">'
        '<div class="context-meta"><span class="label">Supplied context</span>'
        f'<span class="pill">{context.token_count:,} payload tokens</span></div>'
        f'<pre class="payload">{escape(context.payload)}</pre></div><div class="reports">'
        '<select class="repeat-choice" aria-label="Reporting repetition">'
        f"{options}</select>{body}</div></div>"
    )


def _slot(case: ReplayCase, selected: Arm, label: str) -> str:
    options = "".join(
        f'<option value="{arm}"{" selected" if arm == selected else ""}>{arm} · {name}</option>'
        for arm, name in ARM_NAMES.items()
    )
    panels = "".join(_arm(case, arm) for arm in ARM_NAMES)
    return (
        '<div class="comparison-slot"><div class="slot-head">'
        f'<span class="label">{label}</span>'
        f'<select class="arm-choice" aria-label="{label} context">{options}</select>'
        f"</div>{panels}</div>"
    )


def _case(case: ReplayCase, index: int) -> str:
    history = case.history
    truth = derive_truth(history)
    labels = "".join(
        f'<span class="pill">{escape(name.replace("_", " "))}: '
        f"{escape(str(getattr(truth, name)))}</span>"
        for name in STATUS_FIELDS
    )
    events = (
        "".join(
            '<li class="event"><details><summary>'
            f'<span class="event-id">{escape(event.event_id)}</span>'
            f"<b>{escape(event.tool_name)}</b>"
            f'<span class="pill">{"completed" if event.success else "error"}</span>'
            '<span class="event-msg">'
            f"{escape(event.message) if event.tool_name != 'read_file' else 'Read file content'}"
            "</span></summary>"
            f"<pre>{escape(event.model_dump_json(indent=2))}</pre></details></li>"
            for event in history.events
        )
        or '<li class="empty">No tool events</li>'
    )
    return (
        f'<section class="case" data-case="{index}"{" hidden" if index else ""}>'
        '<div class="case-heading"><div>'
        f'<div class="eyebrow">{escape(history.history_id)}</div>'
        f"<h2>{escape(history.task.task_id)}</h2></div>"
        f'<span class="pill">{escape(history.environment.value.replace("_", " "))} · '
        f"{escape(history.termination)}</span></div>"
        f'<p class="task-text">{escape(history.task.description)}</p>'
        f'<div class="truth">{labels}</div>'
        '<div class="label">Independent final-state record above · '
        "report field colors below indicate world accuracy</div>"
        f'<div class="comparison">{_slot(case, Arm.C, "Left")}{_slot(case, Arm.D, "Right")}</div>'
        '<section class="evidence"><h3>Execution evidence</h3>'
        "<p>Every tool result is linked to its source version and supplied test scope.</p>"
        f'<ol class="events">{events}</ol>'
        "<details><summary>Complete native transcript</summary>"
        f"<pre>{escape(chr(10).join(history.messages))}</pre></details></section></section>"
    )


def render_viewer(
    cases: tuple[ReplayCase, ...],
    destination: Path,
    *,
    phase: Literal["development", "heldout"],
    study_note: str = (
        "Results and review status are recorded alongside the raw experiment artifacts."
    ),
    figures: tuple[str, ...] = (),
) -> None:
    """Write one standalone HTML replay; figures are optional local PNG assets."""
    if not cases or len({case.history.history_id for case in cases}) != len(cases):
        raise ValueError("viewer needs nonempty, unique histories")
    for name in figures:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.png", name):
            raise ValueError("figure name must be a local PNG basename")
        if not (destination.parent / "figures" / name).is_file():
            raise ValueError(f"figure is missing: {name}")
    reports = sum(len(case.reports) for case in cases)
    reviewed = sum(
        bool(report.verdict and report.verdict.review_complete)
        for case in cases
        for report in case.reports
    )
    options = "".join(
        f'<option value="{index}">{escape(case.history.history_id)}</option>'
        for index, case in enumerate(cases)
    )
    panels = "".join(_case(case, index) for index, case in enumerate(cases))
    images = "".join(
        f'<img src="figures/{name}" alt="Measured study figure: {name}" loading="lazy">'
        for name in figures
    )
    review_note = (
        "Human review complete for all displayed reports."
        if reports and reviewed == reports
        else "Human prose review pending. Structured-field checks are displayed separately "
        "from the full primary outcome."
    )
    html = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Context Fidelity — {phase} replay</title><style>{_STYLE}</style><body>"
        '<header><div class="brand"><span class="monogram">CF</span>Context Fidelity</div>'
        '<span class="eyebrow">Agent evaluation · Inspect AI</span></header><main>'
        f'<section class="hero"><div><div class="eyebrow">{phase} study</div>'
        "<h1>What survives the summary?</h1>"
        '<p class="lede">One execution. Four views of its history. '
        "Compare what the agent did with what it says it did.</p></div>"
        f'<div class="stats"><div class="stat"><b>{len(cases)}</b><span>histories</span></div>'
        f'<div class="stat"><b>{reports}</b><span>reports</span></div>'
        f'<div class="stat"><b>{reviewed}</b><span>reviewed</span></div></div></section>'
        f'<div class="note pending">{escape(review_note)}</div><div class="toolbar">'
        '<label for="case-choice">Explore a real execution</label>'
        f'<select id="case-choice">{options}</select>'
        '<span class="pill">Offline replay · immutable source records</span></div>'
        f'{panels}<section class="study"><div class="eyebrow">Across the study</div>'
        f"<h2>Results in context</h2><p>{escape(study_note)}</p>{images}</section></main>"
        "<footer>Context Fidelity · Compression occurs only before reporting. "
        "These records do not establish intent or an internal neural mechanism.</footer>"
        f"<script>{_SCRIPT}</script></body></html>"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(html)
