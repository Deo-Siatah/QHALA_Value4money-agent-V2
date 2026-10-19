# The shapes that cross the wire between the API and the React UI.
#
# Two families live here. The snapshot is what the run page reads: everything the committee needs to
# see about a run at one moment, whether it is mid-node, waiting for approval or filed. The events
# are what the SSE connection pushes as the run happens.
#
# Why the events are a discriminated union rather than one loose object: the UI switches on a single
# "type" field, each event carries exactly the fields that event has, and adding an event is one
# class here plus one case in the browser. A field that is always sent but often null is a field the
# UI will get wrong.
#
# Nothing in this file decides anything. It describes a run the agent already made.

from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from mcp_server.tools.submit_for_committee import PLACEHOLDERS

# One run, one order. The terminal CLI prints the same eight and the UI must show the same eight.
STEPS = ("intake", "research", "quality_check", "score", "draft", "verify_sources", "approval", "file")

# The three decisions agent/nodes/approval.py accepts. publish_anyway is the deliberate override a
# person gives for a report that holds no benchmark at all, which is why it is offered here too.
DECISIONS = ("approved", "rejected", "publish_anyway")


class RunStatus(str, Enum):
    """Where a run is, in the one vocabulary the UI switches on."""

    queued = "queued"
    running = "running"
    awaiting_approval = "awaiting_approval"
    filed = "filed"
    rejected = "rejected"
    failed = "failed"


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"


class StepTiming(BaseModel):
    """One of the eight steps, with how long it took and what it found."""

    step: str
    index: int
    status: StepStatus = StepStatus.pending
    elapsed_ms: float = 0.0
    summary: str = ""


class BidView(BaseModel):
    """One bid, with what the statistics said about it and what the model wrote about it."""

    bidder: str
    total: Optional[float] = None
    pct_vs_median: Optional[float] = None
    position: Optional[str] = None
    comment: str = ""
    arithmetic_issues: List[str] = Field(default_factory=list)


class BenchmarkView(BaseModel):
    """The pool's statistics. Every figure is None when the pool was too thin to compute any."""

    n: int = 0
    confidence: str = ""
    median_kes: Optional[float] = None
    q1_kes: Optional[float] = None
    q3_kes: Optional[float] = None


class RunSnapshot(BaseModel):
    """Everything a run page shows at one moment, read from the agent's own state."""

    run_id: str
    status: RunStatus = RunStatus.queued
    tender_ref: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    current_step: Optional[str] = None
    elapsed_ms: float = 0.0
    steps: List[StepTiming] = Field(default_factory=list)
    bids: List[BidView] = Field(default_factory=list)
    benchmark: Optional[BenchmarkView] = None
    pool_size: int = 0 
    pool_values: List[float] = Field(default_factory=list)
    excluded: Dict[str, int] = Field(default_factory=dict)
    summary: str = ""
    bid_comments: Dict[str, str] = Field(default_factory=dict)
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    verify_ok: Optional[bool] = None
    verify_failures: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    parse_issues: List[str] = Field(default_factory=list)
    submission: Optional[Dict[str, Any]] = None


class ApprovalBody(BaseModel):
    """The human decision that resumes a paused run."""

    decision: Literal["approved", "rejected", "publish_anyway"]
    approved_by: str
    note: str = ""

    @field_validator("approved_by")
    @classmethod
    def _a_person(cls, value: str) -> str:
        """Refuse a blank or placeholder name here, so the run is never started into that failure."""
        name = (value or "").strip()
        if len(name) < 2 or name.lower() in PLACEHOLDERS:
            raise ValueError(f"approved_by must be the name of a person, got {value!r}")
        return name


def _number(value: Any) -> Optional[float]:
    """Postgres hands NUMERIC back as Decimal and JSON has no Decimal, so money crosses as float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _steps(current_step: Optional[str], timings: Dict[str, StepTiming]) -> List[StepTiming]:
    """All eight steps in order, with the one in flight marked as running and the rest as they are."""
    ordered = []
    for index, step in enumerate(STEPS):
        timing = timings.get(step, StepTiming(step=step, index=index))
        if timing.status == StepStatus.pending and step == current_step:
            timing = timing.model_copy(update={"status": StepStatus.running})
        ordered.append(timing)
    return ordered


def _bids_benchmark_view(benchmark: Dict[str, Any]) -> Optional[BenchmarkView]:
    return None if not benchmark else BenchmarkView(
        n=benchmark.get("n", 0), confidence=benchmark.get("confidence", ""),
        median_kes=_number(benchmark.get("median_kes")), q1_kes=_number(benchmark.get("q1_kes")),
        q3_kes=_number(benchmark.get("q3_kes")))


def summarise(step: str, update: Dict[str, Any]) -> str:
    """One line saying what a step found, which is all the run page shows beside it.

    These are the same lines the terminal prints at agent/cli.py:118, and
    test_a_step_summary_is_the_line_the_terminal_prints pins the two together. A run page that
    described a step differently from the command line would be showing two versions of the same
    evidence, so the wording is written once, in the terminal, and read from here.
    """
    from agent.cli import summarise

    return summarise(step, update)


def snapshot_from_state(state: Dict[str, Any], status: RunStatus,
                        current_step: Optional[str] = None, elapsed_ms: float = 0.0,
                        timings: Optional[Dict[str, StepTiming]] = None) -> RunSnapshot:
    """Read the agent's state into the shape the run page renders.

    The bid totals and the percentages come from two different places in the state: the bids carry
    what each bidder stated, the benchmark carries what the pool said about it. A bid with no
    benchmark row, which is what an insufficient pool leaves behind, keeps both as None so the UI
    shows nothing claimed rather than a zero it would have to special-case.
    """
    benchmark = state.get("benchmark") or {}
    benchmark_rows = {row.get("bidder"): row for row in benchmark.get("bids") or []}
    comments = state.get("bid_comments") or {}

    bids = []
    for bid in state.get("bids") or []:
        row = benchmark_rows.get(bid.get("bidder")) or {}
        bids.append(BidView(bidder=bid.get("bidder", ""),
                            total=_number(bid.get("stated_total")),
                            pct_vs_median=_number(row.get("pct_vs_median")),
                            position=row.get("position"),
                            comment=comments.get(bid.get("bidder"), "") or "",
                            arithmetic_issues=list(bid.get("issues") or [])))

    pool_values = [_number(row.get("value_kes")) for row in state.get("pool") or []]
    return RunSnapshot(
        run_id=state.get("run_id", ""),
        status=status,
        tender_ref=state.get("tender_ref"),
        category=state.get("category"),
        description=state.get("description"),
        current_step=current_step,
        elapsed_ms=elapsed_ms,
        steps=_steps(current_step, timings or {}),
        bids=bids,
        benchmark=_bids_benchmark_view(benchmark),
        pool_size=state.get("pool_size", 0) or 0,
        pool_values=[value for value in pool_values if value is not None],
        excluded=dict(state.get("excluded") or {}),
        summary=state.get("summary", "") or "",
        bid_comments={str(bidder): str(text or "") for bidder, text in (state.get("bid_comments") or {}).items()},
        findings=list(state.get("findings") or []),
        sources=list(state.get("sources") or []),
        verify_ok=state.get("verify_ok"),
        verify_failures=list(state.get("verify_failures") or []),
        errors=list(state.get("errors") or []),
        parse_issues=list(state.get("parse_issues") or []),
        submission=state.get("submission"),
    )


class RunStarted(BaseModel):
    """The run exists and these are the steps it will take."""

    type: Literal["run.started"] = "run.started"
    run_id: str
    steps: List[str]
    started_at: str


class StepStarted(BaseModel):
    """A node began. LangGraph only reports a node when it finishes, so this is what the UI needs to
    show that a 30 second quality_check is running rather than stalled."""

    type: Literal["step.started"] = "step.started"
    step: str
    index: int
    at: str


class StepFinished(BaseModel):
    """A node finished: how long it took, what it found, and what it returned."""

    type: Literal["step.finished"] = "step.finished"
    step: str
    index: int
    elapsed_ms: float
    summary: str = ""
    data: Optional[Dict[str, Any]] = None


class ModelToken(BaseModel):
    """One piece of the model's own words, as it is written."""

    type: Literal["model.token"] = "model.token"
    step: str
    delta: str


class ModelDone(BaseModel):
    """One model call finished, and which prompt it was."""

    type: Literal["model.done"] = "model.done"
    step: str
    prompt: str = ""
    elapsed_ms: float = 0.0


class ApprovalRequired(BaseModel):
    """The run paused. request is the approval node's own payload, shown to the person deciding."""

    type: Literal["approval.required"] = "approval.required"
    request: Dict[str, Any]


class RunFinished(BaseModel):
    """The run ended one way or another."""

    type: Literal["run.finished"] = "run.finished"
    status: RunStatus
    submission: Optional[Dict[str, Any]] = None


class RunError(BaseModel):
    """Something stopped the run. The message is shown as it is; nothing is softened."""

    type: Literal["run.error"] = "run.error"
    step: Optional[str] = None
    message: str


class Heartbeat(BaseModel):
    """Proof the connection is alive while a long node runs, so the UI can count up honestly."""

    type: Literal["heartbeat"] = "heartbeat"
    elapsed_ms: float = 0.0


RunEvent = Annotated[
    Union[RunStarted, StepStarted, StepFinished, ModelToken, ModelDone,
          ApprovalRequired, RunFinished, RunError, Heartbeat],
    Field(discriminator="type"),
]

RunEventAdapter: TypeAdapter = TypeAdapter(RunEvent)
