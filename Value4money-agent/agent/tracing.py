# Send the run to Opik so its shape can be read: which node, which tool, which prompt, how long.
#
# Tracing is for seeing what the agent did and how slowly. It is never the record of what happened:
# that is audit_log, in Postgres, and it is written whether or not Opik is reachable. A run that
# traces nothing is a run that still filed its evidence.
#
# When Opik is switched off, or no API key is set, every function here becomes the identity and the
# run behaves exactly as it does without tracing. 
# One trace per invocation, with everything nested inside it. The trace is threaded by run_id, so a
# run that pauses for approval and the later command that resumes it read as one conversation.
#
# Where the spans come from:
#   the run    agent/run.py        one trace for a start or a resume
#   a node     agent/graph.py      what each of the eight steps was doing
#   a tool     agent/tools.py every call made to one of the six tools
#   a model    agent/llm.py        every prompt sent to Hasibu

import functools
import inspect
from typing import Any, Callable, Dict, Optional

from langgraph.errors import GraphInterrupt

from config import settings

try:
    import opik
except ImportError:          # Opik is optional: the agent runs without it
    opik = None              # type: ignore[assignment]

_enabled = False


def enabled() -> bool:
    """Whether traces are going anywhere at all."""
    return _enabled


def init_tracing(run_id: Optional[str] = None) -> bool:
    """Point Opik at the configured project. False means tracing is off and nothing changes.

    The run id is accepted for call sites that already have it, but it is not used here: no trace
    exists yet at this point, so a thread id written now would be lost. Call start_trace() from
    inside the tracked entrypoint instead.
    """
    global _enabled
    _enabled = False
    if opik is None or not settings.opik_enabled or not settings.opik_api_key:
        return False

    opik.configure(api_key=settings.opik_api_key,
                   workspace=settings.opik_workspace or None,
                   project_name=settings.opik_project,
                   use_local=False)
    _enabled = True
    reset_trace_tags()
    return True


PAUSED = {"paused_for_approval": True}


def traced(span_type: str = "general", name: Optional[str] = None) -> Callable:
    """Decorate a function so each call is one Opik span of the given kind.

    The span is decided at call time, not when the decorator runs. A module-level decorator is
    evaluated at import, long before init_tracing() is called from main(), so deciding then meant
    every tool and model call was left undecorated and no span was ever sent.
    """
    def decorate(fn: Callable) -> Callable:
        label = name if name is not None else fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _enabled or opik is None:
                return fn(*args, **kwargs)

            def call() -> Any:
                try:
                    result = fn(*args, **kwargs)
                except GraphInterrupt as exc:
                    _quietly(lambda: opik.opik_context.update_current_span(
                        metadata={"outcome": "paused_for_approval"}, error_info=None))
                    # The original exception carries the payload the approver is shown. Raising a
                    # fresh GraphInterrupt here would replace it with an empty one, and the run
                    # would reach its approval gate with nothing to ask about.
                    raise _Paused(exc) from None
                _quietly(lambda: opik.opik_context.update_current_span(
                    metadata={"outcome": "completed"}))
                return result

            try:
                return opik.track(label, type=span_type)(call)()
            except _Paused as paused:
                raise paused.wrapped from None

        wrapper.__signature__ = inspect.signature(fn)
        return wrapper

    return decorate


class _Paused(Exception):
    """Carries a pause past Opik's error handling, so an approval is never logged as a failure."""

    def __init__(self, wrapped: GraphInterrupt) -> None:
        super().__init__("the run paused for a human decision")
        self.wrapped = wrapped


PROJECT_TAG = "value4money"

_tender_recorded = False


def start_trace(run_id: Optional[str], tender_ref: Optional[str] = None) -> None:
    """Name this run's thread, from inside the traced function where a trace actually exists.

    Opik creates the root trace when opik.track runs, so anything written before that lands on
    nothing: init_tracing() used to set the thread id here and it was silently lost, which is why
    every trace arrived with no thread_id and the Threads tab stayed empty. Called from inside the
    tracked entrypoint, the update reaches the trace it belongs to.

    The thread is the run, so a run and any later --resume of it read as one conversation. The
    tender is a tag rather than a thread, because two runs of the same tender are two threads, and
    threading them together would split a run away from its own approval.
    """
    global _tender_recorded
    if not _enabled or opik is None:
        return
    tags = [PROJECT_TAG]
    if tender_ref:
        tags.append(tender_ref)
        _tender_recorded = True
    _quietly(lambda: opik.opik_context.update_current_trace(
        thread_id=run_id, tags=tags))


def record_tender(tender_ref: str) -> None:
    """Tag the trace with the tender, once it has been read.

    The tender reference does not exist until intake has parsed a bid, so it cannot be set when the
    run starts. Tagging it here is what makes runs of one tender comparable in the Opik filters.
    """
    global _tender_recorded
    if not _enabled or opik is None or not tender_ref or _tender_recorded:
        return
    _tender_recorded = True
    _quietly(lambda: opik.opik_context.update_current_trace(tags=[PROJECT_TAG, tender_ref]))


def reset_trace_tags() -> None:
    """Forget which tender was tagged, so the next run in this process starts clean."""
    global _tender_recorded
    _tender_recorded = False


def is_approval_pause(error: str) -> bool:
    """Whether an error string names a pause for approval rather than a failure.

    OpikTracer records any exception as an error, including the interrupt the approval node raises
    on purpose. Left alone, every run that waits for a person would show as failed, which is the one
    thing the error count has to be able to trust.
    """
    return "GraphInterrupt" in (error or "")


def langgraph_tracer() -> Any:
    """The tracer that gives Opik the graph structure, or None when tracing is off.

    The compiled LangGraph app is the only thing that carries the node list and the edges, so
    without this the UI has no graph to draw however well the nodes are traced.
    """
    if not _enabled or opik is None:
        return None
    try:
        from opik.integrations.langchain import OpikTracer
    except ImportError:
        return None
    return OpikTracer(tags=[PROJECT_TAG], skip_error_callback=is_approval_pause)


def track_graph(graph: Any) -> Any:
    """Attach the tracer to a compiled graph, once. Returns the graph unchanged when off."""
    tracer = langgraph_tracer()
    if tracer is None:
        return graph
    try:
        from opik.integrations.langchain import track_langgraph

        return track_langgraph(graph, tracer)
    except Exception:
        return graph


def log_full_prompts(prompt_name: str) -> bool:
    """Whether this prompt's wording is recorded in full, and not just named.

    The search prompt decides what the pool becomes, so a median that moved has to be traceable to
    the words that moved it. Every other prompt carries tender and bid content, which is recorded
    by name and payload shape instead, so a trace store does not hold the whole tender by default.
    """
    return prompt_name in settings.opik_full_prompts


def _record_body(prompt_name: str, body: str):
    """The prompt text, when this prompt is one whose wording may be recorded."""
    return body if log_full_prompts(prompt_name) else None


def log_model_call(prompt_name: str, prompt_text: str, payload: Dict[str, Any],
                   answer: Any = None, **metadata: Any) -> None:
    """Record what was sent to the model and what came back, on the span for this call."""
    if not _enabled:
        return
    body: Dict[str, Any] = {"payload_keys": sorted(payload) if isinstance(payload, dict) else []}
    full = _record_body(prompt_name, prompt_text)
    if full is not None:
        body["system"] = full
    body["payload"] = payload if full is not None else body["payload_keys"]
    _quietly(lambda: opik.opik_context.update_current_span(
        input=body,
        output=answer,
        metadata={"prompt": prompt_name, "full_prompt_recorded": full is not None, **metadata}))


def annotate_llm(prompt_name: str, model: str, provider: str,
                 usage: Optional[dict] = None, **metadata: Any) -> None:
    """Describe the model call in the current span, so Opik can compute its cost."""
    if not _enabled:
        return
    _quietly(lambda: opik.opik_context.update_current_span(
        model=model, provider=provider, usage=usage or {},
        metadata={"prompt": prompt_name, **metadata}))


def record_graph(graph: Any) -> None:
    """Write the graph structure onto the current trace, so the Opik UI can draw it.

    OpikTracer keeps the graph in the metadata of a trace it opens itself. The run is already
    inside the agent_run trace when LangGraph starts, so the tracer joins that trace and the graph
    is never written. Setting it here, from inside agent_run, puts it on the trace the UI shows.
    """
    if not _enabled or opik is None:
        return
    _quietly(lambda: opik.opik_context.update_current_trace(metadata={
        "_opik_graph_definition": {
            "format": "mermaid",
            "data": graph.get_graph(xray=True).draw_mermaid(),
        }}))


def trace_run(run_id: Optional[str], **metadata: Any) -> None:
    """Thread the trace by run_id, so a pause and its later approval read as one conversation."""
    if not _enabled or not run_id:
        return
    _quietly(lambda: opik.opik_context.update_current_trace(thread_id=run_id, metadata=metadata))


def _quietly(action: Callable[[], Any]) -> None:
    """Do the Opik side of things without ever letting them break a run.

    Annotating a span only works inside one. Called outside, Opik raises, and a trace is not worth
    failing a tender review over.
    """
    try:
        action()
    except Exception:
        pass


def flush() -> None:
    """Send everything queued before the process ends. The SDK batches, so this matters."""
    if _enabled:
        opik.flush_tracker()
