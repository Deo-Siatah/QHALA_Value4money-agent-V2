"""Start, watch and resume one run.

The agent cannot be driven from an async handler: graph.stream blocks, psycopg blocks and every tool
blocks. So a run lives on a thread of its own from this module's ThreadPoolExecutor, and the HTTP
layer only ever touches the registry and the event channel here.

One run, one thread_id, one channel. A run that stops at the approval interrupt is not finished: it
keeps its channel open and its thread released, and the decision that arrives later resumes the very
same checkpointed run. That is the whole reason this module exists, because the approval is the one
place a human is allowed into the process.

Nothing here decides anything. The graph decides, the approval node decides, and this only carries
what they decided to the browser.
"""

from __future__ import annotations

import itertools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langgraph.types import Command

from config import settings
from agent.state import initial_state
from api import models
from api.events import RunEventChannel, RunEventHandler

# LangGraph's default recursion limit counts super-steps; a run that loops research three times and
# redrafts twice needs fewer than this, and a runaway loop should stop rather than hang a thread.
RECURSION_LIMIT = 50


class RunError(RuntimeError):
    """A run could not be started, resumed or found. The message is shown as it is."""


class Run:
    """One run: its id, its files, where it got to, and the channel its events arrive on."""

    def __init__(self, run_id: str, pdf_paths: List[str], sequence: int = 0) -> None:
        self.run_id = run_id
        self.pdf_paths = list(pdf_paths)
        self.sequence = sequence
        self.status = models.RunStatus.queued
        self.channel = RunEventChannel()
        self.handler = RunEventHandler(self.channel)
        self.state: Dict[str, Any] = {"run_id": run_id}
        self.timings: Dict[str, models.StepTiming] = {}
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: Optional[str] = None
        self.started_monotonic: Optional[float] = None
        self.started_emitted = False
        self.approval_request: Optional[Dict[str, Any]] = None
        self.busy = False
        self.lock = threading.Lock()

    @property
    def waiting_for_a_person(self) -> bool:
        """Whether the run is paused at the approval interrupt and can be resumed."""
        return self.status == models.RunStatus.awaiting_approval

    def elapsed_ms(self) -> float:
        """How long the run has been going, used for the clock the UI shows while nothing finishes."""
        if self.started_monotonic is None:
            return 0.0
        return (time.monotonic() - self.started_monotonic) * 1000


class Runner:
    """Owns the runs this process is holding and the threads they run on."""

    def __init__(self, graph: Any, max_concurrency: Optional[int] = None) -> None:
        """The graph this runner drives, and how many runs may be on a thread at once.

        The cap comes from config.py next to every other limit, because it is a number an operator
        changes when the database is small, not part of how a run works.
        """
        self.graph = graph
        self.max_concurrency = (settings.run_max_concurrency if max_concurrency is None
                                else max_concurrency)
        self._runs: Dict[str, Run] = {}
        self._lock = threading.Lock()
        self._sequence = itertools.count(1)
        self._executor = ThreadPoolExecutor(max_workers=max(1, self.max_concurrency),
                                            thread_name_prefix="v4m-run")

    def shutdown(self) -> None:
        """Stop taking new runs and let the threads finish what they are doing."""
        self._executor.shutdown(wait=False)

    # ---- the registry -------------------------------------------------------------------

    def get(self, run_id: str) -> Run:
        """The run with this id, or a refusal naming the id rather than a bare 404."""
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise RunError(f"no run {run_id!r}: it never started, or this process no longer holds it")
        return run

    def all_runs(self) -> List[Run]:
        """Every run this process is holding, newest first.

        Ordered by a counter rather than by the timestamp a run was stamped with, because two runs
        started in the same microsecond would otherwise come back in an arbitrary order.
        """
        with self._lock:
            runs = list(self._runs.values())
        return sorted(runs, key=lambda run: run.sequence, reverse=True)

    # ---- starting and resuming -----------------------------------------------------------

    def start(self, run_id: str, pdf_paths: List[str]) -> Run:
        """Claim a run id, put the run on a thread, and return at once. The events are on its channel.

        The id is the caller's, and a caller must never take it from a request body: it becomes a
        thread_id and a directory name. Registering here rather than separately means a run that is
        going cannot exist outside the registry, which is what stops one run being driven twice.
        """
        if not pdf_paths:
            raise RunError(f"run {run_id} has no bid PDFs to read")
        with self._lock:
            if run_id in self._runs:
                raise RunError(f"run {run_id} already exists")
            run = Run(run_id, pdf_paths, next(self._sequence))
            self._runs[run_id] = run
        run.started_monotonic = time.monotonic()
        self._executor.submit(self._drive, run, None, None)
        return run

    def resume(self, run_id: str, decision: Dict[str, Any]) -> Run:
        """Send a human's decision back into the run that is waiting for it.

        The run has to be paused at the approval interrupt and it must not already be moving, or the
        same run would be driven by two threads at once and could file twice.
        """
        run = self.get(run_id)
        if not run.waiting_for_a_person:
            raise RunError(f"run {run_id} is {run.status.value}, not waiting for a decision")
        with run.lock:
            if run.busy:
                raise RunError(f"run {run_id} is already being resumed")
            run.busy = True
        self._executor.submit(self._drive, run, Command(resume=decision), None)
        return run

    # ---- the work itself -----------------------------------------------------------------

    def _config(self, run: Run) -> Dict[str, Any]:
        """The LangGraph config for this run: its thread id, its callbacks, and a loop bound."""
        return {"configurable": {"thread_id": run.run_id},
                "callbacks": [run.handler],
                "recursion_limit": RECURSION_LIMIT}

    def _drive(self, run: Run, command: Optional[Command], resume_state: Optional[Dict[str, Any]]) -> None:
        """Run the graph to its next pause, on this thread, and report what happened.

        Called with a command it resumes a paused run, and with an initial state it starts one.
        Either way it stops at the next interrupt, and either way it leaves the channel open when it
        does: the run is not over, a person still has to answer.
        """
        config = self._config(run)
        run.status = models.RunStatus.running
        if command is None and not run.started_emitted:
            run.started_emitted = True
            run.channel.emit(models.RunStarted(run_id=run.run_id, steps=list(models.STEPS),
                                               started_at=run.started_at))

        payload: Any = command if command is not None else initial_state(
            run.run_id, run.pdf_paths, run.started_at)

        try:
            for chunk in self.graph.stream(payload, config, stream_mode="updates"):
                if not self._read(chunk, run):
                    break
        except Exception as exc:  # the run failed; the browser is told in plain words
            run.status = models.RunStatus.failed
            run.channel.emit(models.RunError(step=self._current_step(run), message=f"{type(exc).__name__}: {exc}"))
            self._collect(run, config)
            self._finish(run)
            return

        self._collect(run, config)
        with run.lock:
            run.busy = False
        if run.waiting_for_a_person:
            return
        self._finish(run)

    def _read(self, chunk: Any, run: Run) -> bool:
        """Read one thing the graph said. False means the run paused and the loop should stop."""
        if not isinstance(chunk, dict):
            return True
        interrupts = chunk.get("__interrupt__")
        if interrupts:
            run.approval_request = interrupts[0].value
            run.status = models.RunStatus.awaiting_approval
            run.channel.emit(models.ApprovalRequired(request=run.approval_request))
            return False
        for name, update in chunk.items():
            if isinstance(update, dict) and name in models.STEPS:
                run.state.update(update)
        return True

    def _collect(self, run: Run, config: Dict[str, Any]) -> None:
        """Take the timings the handler gathered and the state the checkpoint now holds.

        The handler records what each node returned in the same order it records the timings, so the
        one line the run page shows beside a step can be read out of the node's own answer.
        """
        for record, data in zip(run.handler.steps, run.handler.step_data):
            run.timings[record.step] = models.StepTiming(
                step=record.step, index=record.index, status=models.StepStatus.done,
                elapsed_ms=record.elapsed_ms, summary=models.summarise(record.step, data or {}))
        try:
            values = self.graph.get_state(config).values
        except Exception:
            return
        if isinstance(values, dict):
            run.state.update(values)

    @staticmethod
    def _current_step(run: Run) -> Optional[str]:
        """The step that was in flight when the run stopped, which is what the UI should show."""
        started = run.handler.step_started
        return started[0] if started else None

    def _finish(self, run: Run) -> None:
        """Say how the run ended, and close the channel so the browser stops listening.

        Only a run that produced a submission is filed. A person who declined is a rejection, and
        anything else that ended without filing is a failure rather than a decision: agent/run.py
        prints those as "filed nothing", and calling them rejected would put a word in a person's
        mouth that they never said.
        """
        if run.status == models.RunStatus.running:
            if run.state.get("submission"):
                run.status = models.RunStatus.filed
            elif run.state.get("approval_status") == "rejected":
                run.status = models.RunStatus.rejected
            else:
                run.status = models.RunStatus.failed
        run.channel.emit(models.RunFinished(status=run.status, submission=run.state.get("submission")))
        run.finished_at = datetime.now(timezone.utc).isoformat()
        run.channel.close()

    # ---- what the API reads --------------------------------------------------------------

    def snapshot(self, run_id: str) -> models.RunSnapshot:
        """The run as it stands, in the shape the run page renders."""
        run = self.get(run_id)
        current = self._current_step(run)
        return models.snapshot_from_state(run.state, status=run.status, current_step=current,
                                          elapsed_ms=run.elapsed_ms(), timings=run.timings)

    def channel_for(self, run_id: str) -> RunEventChannel:
        """The channel a run's events arrive on, for the SSE connection to read."""
        return self.get(run_id).channel
