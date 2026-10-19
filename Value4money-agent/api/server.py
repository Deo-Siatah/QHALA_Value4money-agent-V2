# The HTTP API the React UI talks to.
#
# This layer decides nothing and computes nothing. It accepts bid PDFs, starts a run, forwards the
# events the run produces, hands a person's decision back to the run that is waiting for it, and
# serves the evidence file the agent wrote. Every figure, every exclusion and every refusal still
# comes from the agent and the tools, so the browser and the terminal are looking at the same run.
#
# Three things are load-bearing here:
#
#   The token. Approval writes a row to reports and two to audit_log, permanently. Nothing is served
#   without X-API-Token, and the server refuses to start at all when API_TOKEN is unset, because an
#   evidence file is not something to leave open on a network.
#
#   The uploads folder. settings.bids_dir is pointed at settings.uploads_dir once, at startup, and
#   each run's PDFs go under uploads/<run_id>/. parse_bid_pdf refuses any path outside bids_dir, so
#   this is what lets an uploaded file be read by the ordinary audited tool with no special case and
#   no per-request change to a process-wide setting, which is what makes concurrent runs safe.
#
#   The run id. It is the server's to choose. It becomes a checkpoint thread_id and a directory
#   name, so a caller-supplied id is refused rather than trusted.

import asyncio
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from agent.checkpointer import close_shared_saver, shared_saver
from agent.graph import build_graph
from agent.nodes.approval import HAS_EVIDENCE, PUBLISH_ANYWAY, needs_publish_anyway
from api import models
from api.events import RunEventChannel
from api.reports import render_pdf, render_tex, review_from_state
from api.runner import Runner, Run, RunError

TOKEN_HEADER = "X-API-Token"

# The run id becomes a thread_id and a directory name, so it is generated here and checked if a
# caller insists on one. submit_for_committee accepts [A-Za-z0-9_-]{1,64}, and this is stricter still.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# A tender bid is a few hundred kilobytes. Twenty megabytes per file is far above any real one and
# low enough that a stray upload cannot fill the disk the runs are written on.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# How long the SSE stream waits before sending a heartbeat of its own, so a forty second node with
# nothing to say still looks alive in the browser.
HEARTBEAT_SECONDS = 10.0

# What a person is told when they try to approve a report that rests on no benchmark. It names the
# other answer rather than only refusing this one.
NO_EVIDENCE_REFUSAL = (f"this report has no comparable awards, so it holds no evidence. "
                       f"Send decision {PUBLISH_ANYWAY!r} to file it anyway, or 'rejected' to stop.")


def new_run_id() -> str:
    """A run id for this process to own: the time, so ids sort, and a random tail so two uploads in
    the same second cannot collide."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"run-{stamp}-{secrets.token_hex(4)}"


def _origins() -> List[str]:
    """The browser origins allowed to talk to this API. Never a wildcard: approval is a real gate."""
    return [origin.strip() for origin in settings_cors().split(",") if origin.strip()]


def settings_cors() -> str:
    from config import settings
    return settings.cors_origins


def require_token(request: Request) -> None:
    """Refuse anything without the shared token, in constant time."""
    from config import settings

    if not settings.api_token:
        raise HTTPException(status_code=401, detail="API_TOKEN is not set on the server")
    given = request.headers.get(TOKEN_HEADER, "")
    if not secrets.compare_digest(given, settings.api_token):
        raise HTTPException(status_code=401, detail="a valid X-API-Token header is required")


async def _save_upload(upload: UploadFile, folder: Path) -> str:
    """Write one uploaded bid into this run's folder and return the absolute path to read it from.

    Only the file's own name is used, never a path the browser sent: the name becomes part of a
    directory path, and a caller must not be able to choose where on this machine a file lands.
    """
    name = Path(upload.filename or "").name
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail=f"{name or 'file'} is not a PDF")
    body = await upload.read()
    if not body:
        raise HTTPException(status_code=400, detail=f"{name} is empty")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{name} is larger than "
                                                   f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(body)
    return str(path.resolve())


async def _events(channel: RunEventChannel, elapsed: Any,
                  heartbeat: float = HEARTBEAT_SECONDS) -> AsyncIterator[Dict[str, Any]]:
    """Everything the run has to say, as server-sent events, with a heartbeat while it is quiet.

    Each event carries its own type as the SSE event name and its fields as the JSON data, so the
    browser can switch on the name and read the same model the API validates against.
    """
    while True:
        try:
            event = await channel.get(heartbeat)
        except asyncio.TimeoutError:
            yield {"event": "heartbeat",
                   "data": models.Heartbeat(elapsed_ms=elapsed()).model_dump_json()}
            continue
        if event is None:
            return
        yield {"event": event.type, "data": event.model_dump_json()}


def create_app(runner: Optional[Runner] = None) -> FastAPI:
    """Build the API. A runner may be handed in so a test can drive a run without a database."""
    from config import settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not settings.api_token:
            raise RuntimeError("API_TOKEN is not set: this API can approve and file evidence files, "
                               "so it refuses to start without a token")
        app.state.runner = runner or Runner(build_graph(shared_saver()),
                                            max_concurrency=settings.run_max_concurrency)
        app.state.saver_open = runner is None
        if runner is None:
            # One setting, set once. Every run's files sit under it, so absolute paths validate in
            # parse_bid_pdf without anything being mutated per request.
            settings.bids_dir = settings.uploads_dir
        try:
            yield
        finally:
            if app.state.saver_open:
                close_shared_saver()

    app = FastAPI(title="Hasibu API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware,
                       allow_origins=_origins(),
                       allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["Content-Type", TOKEN_HEADER],
                       allow_credentials=False)

    def runs(request: Request) -> Runner:
        """The runner this process is holding, however it was built."""
        return request.app.state.runner

    @app.get("/api/health")
    async def health() -> Dict[str, Any]:
        """Whether the API is up. The one route with no token, because it reveals nothing."""
        return {"status": "ok", "steps": list(models.STEPS)}

    @app.post("/api/runs", status_code=202, dependencies=[Depends(require_token)])
    async def create_run(request: Request,
                         files: List[UploadFile] = File(default=[]),
                         run_id: Optional[str] = Form(default=None)) -> Dict[str, Any]:
        """Take the bid PDFs and start a run. Returns at once; the events carry the rest."""
        if not files:
            raise HTTPException(status_code=400, detail="at least one bid PDF is required")
        chosen = run_id or new_run_id()
        if not RUN_ID_PATTERN.match(chosen):
            raise HTTPException(status_code=400,
                                detail=f"run_id {chosen!r} must be letters, digits, - or _")
        folder = settings.uploads_dir / chosen
        paths = [await _save_upload(file, folder) for file in files]
        try:
            run = runs(request).start(chosen, paths)
        except RunError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": run.run_id, "status": run.status.value,
                "steps": list(models.STEPS), "uploaded": len(paths)}

    @app.get("/api/runs", dependencies=[Depends(require_token)])
    async def list_runs(request: Request) -> List[Dict[str, Any]]:
        """Every run this process is holding, newest first."""
        return [{"run_id": run.run_id, "status": run.status.value,
                 "tender_ref": run.state.get("tender_ref"),
                 "started_at": run.started_at,
                 # The moment the run ended, so the list can show a duration rather than a stopwatch
                 # that keeps counting on a run that finished long ago.
                 "finished_at": run.finished_at,
                 "awaiting_approval": run.waiting_for_a_person}
                for run in runs(request).all_runs()]

    @app.get("/api/runs/{run_id}", dependencies=[Depends(require_token)])
    async def get_run(request: Request, run_id: str) -> models.RunSnapshot:
        """The run as it stands: which step, how long each took, and everything found so far."""
        return _snapshot(runs(request), run_id)

    @app.get("/api/runs/{run_id}/state", dependencies=[Depends(require_token)])
    async def get_state(request: Request, run_id: str) -> Dict[str, Any]:
        """The agent's own state for this run, for when the snapshot is not enough."""
        return runs(request).get(run_id).state

    @app.get("/api/runs/{run_id}/events", dependencies=[Depends(require_token)])
    async def stream_events(request: Request, run_id: str) -> EventSourceResponse:
        """The run as it happens. One connection carries the whole run, including the pause at the
        approval gate and everything after the decision comes back."""
        channel = _channel_for(runs(request), run_id)
        return EventSourceResponse(_events(channel, runs(request).get(run_id).elapsed_ms),
                                   ping=HEARTBEAT_SECONDS * 2)

    @app.post("/api/runs/{run_id}/approval", status_code=202,
              dependencies=[Depends(require_token)])
    async def decide(request: Request, run_id: str, body: models.ApprovalBody) -> Dict[str, Any]:
        """Hand a person's decision to the run that is waiting for it.

        A report with no comparable awards in it holds no evidence, so plain approval of it is
        refused here with a message that says what to do instead. The same rule is the approval
        node's; this check exists so the person is told at the moment they press the button rather
        than after the run has been resumed and died, and it asks agent/nodes/approval.py for the
        answer so the two cannot disagree.

        The run also refuses a decision it is not waiting for, and one already moving, because the
        same run driven twice could file twice.
        """
        run = _run_or_404(runs(request), run_id)
        if not run.waiting_for_a_person:
            raise HTTPException(status_code=409,
                                detail=f"run {run_id} is {run.status.value}, not waiting for a decision")
        status = (run.approval_request or {}).get("evidence_status", HAS_EVIDENCE)
        if body.decision == "approved" and needs_publish_anyway(status):
            raise HTTPException(status_code=422, detail=NO_EVIDENCE_REFUSAL)
        try:
            resumed = runs(request).resume(run_id, {"decision": body.decision,
                                                    "approved_by": body.approved_by,
                                                    "note": body.note})
        except RunError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"run_id": resumed.run_id, "status": resumed.status.value,
                "approved_by": body.approved_by}

    @app.get("/api/runs/{run_id}/report", dependencies=[Depends(require_token)])
    async def get_report(request: Request, run_id: str) -> Any:
        """The evidence file, exactly as the agent rendered it. Not reformatted for the browser."""
        from fastapi.responses import PlainTextResponse

        run = runs(request).get(run_id)
        markdown = run.state.get("review_markdown") or ""
        if not markdown.strip():
            raise HTTPException(status_code=409,
                                detail="this run has no report yet: it is still working, or it was "
                                       "never approved")
        return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")

    @app.get("/api/runs/{run_id}/report.tex", dependencies=[Depends(require_token)])
    async def get_report_tex(request: Request, run_id: str) -> Any:
        """The same evidence file as LaTeX source, for a committee that keeps the file itself.

        There is no TeX engine in this environment, so this is source to compile elsewhere, not a
        compiled document.
        """
        from fastapi.responses import Response

        tex = render_tex(_review_for(runs(request), run_id))
        return Response(tex, media_type="application/x-tex; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{run_id}.tex"'})

    @app.get("/api/runs/{run_id}/report.pdf", dependencies=[Depends(require_token)])
    async def get_report_pdf(request: Request, run_id: str) -> Any:
        """The same evidence file as a PDF, rendered in pure Python with the figures the agent used.

        Not the browser's print of the markdown: this one is laid out by the server, so it prints the
        same way on a committee member's machine as on the server's.
        """
        from fastapi.responses import Response

        data = render_pdf(_review_for(runs(request), run_id))
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{run_id}.pdf"'})

    return app


def _review_for(runner: Runner, run_id: str) -> Dict[str, Any]:
    """The review dict for a run, refusing plainly when there is no report to render.

    All three downloads go through here, so a run with no report answers the same way whichever
    format was asked for, rather than raising somewhere inside a renderer.
    """
    run = _run_or_404(runner, run_id)
    if not (run.state.get("review_markdown") or "").strip():
        raise HTTPException(status_code=409,
                            detail="this run has no report yet: it is still working, or it was "
                                   "never approved")
    return review_from_state(run.state)


def _channel_for(runner: Runner, run_id: str) -> RunEventChannel:
    """The channel a run's events arrive on, turning the runner's refusal into a 404 naming the run."""
    try:
        return runner.channel_for(run_id)
    except RunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _run_or_404(runner: Runner, run_id: str) -> Run:
    """One run, turning the runner's refusal into a 404 that names the run."""
    try:
        return runner.get(run_id)
    except RunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _snapshot(runner: Runner, run_id: str) -> models.RunSnapshot:
    """Read one run for the run page, turning the runner's refusal into a 404 that names the run."""
    try:
        return runner.snapshot(run_id)
    except RunError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


app = create_app()
