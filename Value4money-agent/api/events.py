# Getting one run's events from the agent's worker thread to a browser on the event loop.
#
# The agent does not do anything asynchronous. graph.stream blocks, psycopg blocks, and every tool
# blocks, so a run has to run on a thread of its own while the SSE connection waits on the loop.
# That leaves one problem, and these two classes are the answer to it: how an event crosses from the
# thread that made it to the loop that will send it, without either waiting on the other.
#
# RunEventChannel is the crossing. emit() may be called from any thread and never blocks; the loop
# side reads with get() or events(). If the reader falls behind, the oldest events are dropped rather
# than allowed to fill up and stall the run, because a run is not worth delaying for a browser tab,
# and the authoritative record is audit_log and the report, not this stream.
#
# RunEventHandler is the listener. LangChain calls it from inside the agent's own thread as each node
# and each model call starts, streams and ends, and it turns those into the events above. It is the
# same idea as the terminal CLI's TokenPrinter, except that it also times the nodes: LangGraph only
# reports a node when it finishes, so the UI would otherwise have nothing to show for the 40 seconds
# quality_check takes.

import asyncio
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Dict, List, NamedTuple, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler

from api import models

# Tells an empty buffer from a closed one, which both return None from _take but mean opposite things.
_NOTHING = object()


def _now() -> str:
    """The moment an event happened, in the ISO form the UI and the audit log both use."""
    return datetime.now(timezone.utc).isoformat()


class RunEventChannel:
    """One run's events, buffered here and read on the event loop.

    Constructed where a loop is running, which is inside the request handler that opens the SSE
    connection, though it does not need one to exist: the buffer is a plain deque and nothing is
    handed to the loop until something reads it.

    Events are appended by the agent's thread under a lock, so an event is in the buffer the moment
    emit returns. That matters: a run that finishes and closes must not lose its own last event to a
    hand-off that has not happened yet.

    If nobody is reading and the buffer fills, the oldest event is dropped rather than blocking the
    run. A run is not worth delaying for a browser tab, and the authoritative record is audit_log
    and the filed report, not this stream.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._items: deque = deque()
        self._lock = threading.Lock()
        self._maxsize = max(1, maxsize)
        self._closed = False
        self._wakeup: Optional[asyncio.Event] = None
        self.dropped = 0

    @property
    def closed(self) -> bool:
        """Whether the run is over and no further event will be accepted."""
        return self._closed

    def emit(self, event: models.BaseModel) -> None:
        """Add one event. Safe from any thread, and never blocks the caller."""
        with self._lock:
            if self._closed:
                return
            self._items.append(event)
            while len(self._items) > self._maxsize:
                self._items.popleft()
                self.dropped += 1
        self._wake()

    def _wake(self) -> None:
        """Nudge a reader waiting on the loop, from whatever thread emit was called on."""
        wakeup = self._wakeup
        if wakeup is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.call_soon_threadsafe(wakeup.set)
        else:
            wakeup.set()

    def _take(self) -> Optional[models.BaseModel]:
        """The next buffered event, or None when there is nothing and the run is over."""
        with self._lock:
            if self._items:
                return self._items.popleft()
            return None if self._closed else _NOTHING

    async def get(self, timeout: Optional[float] = None) -> Optional[models.BaseModel]:
        """The next event, or None once the run is over and the buffer has drained.

        A timeout is how the SSE connection learns that a long node is still running: quality_check
        can take forty seconds and says nothing at all while it does, so the stream sends a heartbeat
        of its own rather than letting the browser wonder whether the connection died.
        """
        if self._wakeup is None:
            self._wakeup = asyncio.Event()
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            event = self._take()
            if event is not _NOTHING:
                return event
            self._wakeup.clear()
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise asyncio.TimeoutError
            try:
                await asyncio.wait_for(self._wakeup.wait(),
                                       timeout=0.5 if remaining is None else min(0.5, remaining))
            except asyncio.TimeoutError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise
                continue

    async def events(self) -> AsyncIterator[models.BaseModel]:
        """Every event until the run ends, in the order it happened."""
        while True:
            event = await self.get()
            if event is None:
                return
            yield event

    def close(self) -> None:
        """Say the run is over. Safe from any thread, and safe to call more than once."""
        with self._lock:
            self._closed = True
        self._wake()


class StepRecord(NamedTuple):
    """One step that ran, as the timing the UI shows beside it."""

    step: str
    index: int
    elapsed_ms: float


class RunEventHandler(BaseCallbackHandler):
    """Turns one run's LangChain callbacks into events on a channel.

    Callbacks arrive from the agent's thread, so nothing here may block. Each of them records what it
    saw as well as emitting, because a test needs to read the timings without a loop, and because a
    trace is worth keeping when the channel is gone.
    """

    raise_error = False

    def __init__(self, channel: RunEventChannel, clock: Callable[[], float] = time.monotonic) -> None:
        """The clock is a parameter so a test can measure a step exactly.

        On Windows time.monotonic ticks about every 15.6ms, so a test that sleeps for ten and asserts
        on the result passes or fails depending on the machine. A run that takes thirty seconds is
        not the thing that needs proving here; the arithmetic is.
        """
        self.channel = channel
        self.clock = clock
        self.steps: List[StepRecord] = []
        self.step_started: Optional[Tuple[str, int]] = None
        self.step_data: List[Optional[Dict[str, Any]]] = []
        self.tokens: List[str] = []
        self.model_prompts: List[str] = []
        self.model_times: List[Tuple[str, float]] = []
        self._open_steps: Dict[Any, Tuple[str, int, float]] = {}
        self._open_models: Dict[Any, Tuple[str, float]] = {}

    def _emit(self, event: models.BaseModel) -> None:
        """Send an event, or lose it.

        A channel that has gone away is a trace that cannot be written, and a trace is not worth
        failing a tender review over: the same rule agent/tracing.py applies to Opik. The timings
        this handler records are kept either way, so a dropped event costs the UI a line and nothing
        else.
        """
        try:
            self.channel.emit(event)
        except Exception:
            pass

    @property
    def current_step(self) -> str:
        """The step a token belongs to, for labelling the model's own words."""
        return self._open_steps[self._current_id][0] if self._open_steps else ""

    @property
    def _current_id(self) -> Any:
        return next(reversed(self._open_steps), None)

    @staticmethod
    def _name(kwargs: Dict[str, Any], serialized: Any) -> str:
        """The node or model name, which LangChain passes as a kwarg and sometimes in the payload."""
        name = kwargs.get("name")
        if name:
            return str(name)
        if isinstance(serialized, dict) and serialized.get("name"):
            return str(serialized["name"])
        return ""

    def on_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
        """A chain began. Only the eight named steps of a run are steps; the rest are LangGraph's."""
        name = self._name(kwargs, serialized)
        if name not in models.STEPS:
            return
        run_id = kwargs.get("run_id")
        index = models.STEPS.index(name)
        self._open_steps[run_id] = (name, index, self.clock())
        self.step_started = (name, index)
        self._emit(models.StepStarted(step=name, index=index, at=_now()))

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        """A chain ended. A chain that never started is dropped: it has no elapsed time to claim."""
        run_id = kwargs.get("run_id")
        opened = self._open_steps.pop(run_id, None)
        if opened is None:
            return
        name, index, started_at = opened
        elapsed_ms = (self.clock() - started_at) * 1000
        data = outputs if isinstance(outputs, dict) else None
        self.steps.append(StepRecord(name, index, elapsed_ms))
        self.step_data.append(data)
        self._emit(models.StepFinished(step=name, index=index, elapsed_ms=elapsed_ms, data=data))

    def on_llm_start(self, serialized: Any, prompts: Any = None, **kwargs: Any) -> None:
        """A model call began, so its words can be timed and attributed to a step."""
        run_id = kwargs.get("run_id")
        self._open_models[run_id] = (self._name(kwargs, serialized), self.clock())

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """One piece of the model's answer. stream may deliver it as bytes."""
        if isinstance(token, bytes):
            token = token.decode("utf-8", errors="replace")
        if not token:
            return
        self.tokens.append(token)
        self._emit(models.ModelToken(step=self.current_step, delta=token))

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """A model call finished. Which prompt it was is more useful to the UI than its length."""
        run_id = kwargs.get("run_id")
        opened = self._open_models.pop(run_id, None)
        if opened is None:
            return
        name, started_at = opened
        elapsed_ms = (self.clock() - started_at) * 1000
        self.model_prompts.append(name)
        self.model_times.append((name, elapsed_ms))
        self._emit(models.ModelDone(step=self.current_step, prompt=name, elapsed_ms=elapsed_ms))
