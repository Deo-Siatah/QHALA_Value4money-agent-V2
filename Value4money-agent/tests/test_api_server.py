# The HTTP surface the React UI talks to.
#
# Everything here is tested with a fake runner, so no test starts a real agent run, reaches a
# database or needs a model. What is being pinned is the contract: what a request is allowed to do,
# what it is refused, and that the approval gate cannot be walked past.

import asyncio
import io

import pytest
from fastapi.testclient import TestClient

from api import models
from api.runner import Runner
from api.events import RunEventChannel
from api.server import _events, create_app
from config import settings

TOKEN = "test-token"
APPROVAL = {"tender_ref": "T/1", "summary": "Twelve awards were compared.",
            "benchmark": {"n": 12, "confidence": "medium", "median_kes": 4000000.0,
                          "q1_kes": 3000000.0, "q3_kes": 5000000.0},
            "bids": [], "evidence_status": "has_evidence", "verify_failures": []}

# The state a run holds once the draft node has rendered a report: everything the three downloads
# are built from, which is exactly what the agent keeps.
REPORTED_STATE = {
    "tender_ref": "T/1",
    "summary": "Twelve awards of classroom construction were compared.",
    "bid_comments": {"Apex": "4.2% above the median."},
    "benchmark": {"n": 12, "confidence": "medium", "median_kes": 4000000.0,
                  "q1_kes": 3000000.0, "q3_kes": 5000000.0,
                  "bids": [{"bidder": "Apex", "total": 4000000.0, "pct_vs_median": 4.2,
                            "position": "within_range"}]},
    "bids": [{"bidder": "Apex", "stated_total": 4000000.0, "items": [],
              "computed_total": 4000000.0, "arithmetic_ok": True, "issues": []}],
    "search": {"match": "loose", "category": "works"},
    "excluded": {"different_work": 2},
    "findings": [{"text": "One bid is 4.2% above the median.", "sources": ["a01"]}],
    "sources": [{"award_id": "a01", "ocid": "ocid-a01", "data_year": 2024,
                 "buyer_name": "County Government", "title": "Classroom block",
                 "value_kes": 4000000.0}],
    "review_markdown": "# Price review evidence: T/1\n",
}


class Interrupt:
    def __init__(self, value):
        self.value = value


class PausedGraph:
    """A graph that goes straight to the approval gate, which is where every real run ends up.

    Pausing rather than finishing is what makes these tests deterministic: the run then stays in
    awaiting_approval however fast the machine is, so nothing here races the thread doing the work.
    """

    def __init__(self, approval=APPROVAL):
        self.approval = approval

    def stream(self, payload, config, stream_mode=None):
        for callback in config.get("callbacks") or []:
            callback.on_chain_start({}, {}, run_id="n1", name="intake")
            callback.on_chain_end({"bids": [{"bidder": "Apex", "stated_total": 100.0}]}, run_id="n1")
        return iter([{"__interrupt__": (Interrupt(self.approval),)}])

    def get_state(self, config):
        class State:
            values = {}
        return State()


def _make_client(tmp_path, monkeypatch, graph):
    """A TestClient over a runner with no database behind it and uploads in a temporary folder."""
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "api_token", TOKEN)
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:5173")
    runner = Runner(graph)
    with TestClient(create_app(runner=runner)) as test_client:
        test_client.headers.update({"X-API-Token": TOKEN})
        test_client.runner = runner
        yield test_client


@pytest.fixture
def client(tmp_path, monkeypatch):
    yield from _make_client(tmp_path, monkeypatch, PausedGraph())


@pytest.fixture
def thin_pool_client(tmp_path, monkeypatch):
    """The same, for a run whose pool was too thin to benchmark anything."""
    thin = dict(APPROVAL, evidence_status="no_evidence",
                benchmark={"n": 2, "confidence": "insufficient"})
    yield from _make_client(tmp_path, monkeypatch, PausedGraph(thin))


def pdf_upload(*names):
    """Bid PDFs as the browser would send them: one multipart part per file, all named 'files'."""
    from fpdf import FPDF

    parts = []
    for name in (names or ("apex.pdf",)):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, txt="Bidder: Apex", ln=True)
        parts.append(("files", (name, io.BytesIO(bytes(pdf.output())), "application/pdf")))
    return parts


def test_the_latex_download_carries_the_figures_and_says_what_it_is(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-tex"})
    client.runner.get("run-tex").state.update(REPORTED_STATE)

    response = client.get("/api/runs/run-tex/report.tex")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-tex")
    assert 'filename="run-tex.tex"' in response.headers["content-disposition"]
    assert "\\documentclass" in response.text
    assert "4,000,000.00" in response.text


def test_the_pdf_download_is_a_pdf(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-pdf"})
    client.runner.get("run-pdf").state.update(REPORTED_STATE)

    response = client.get("/api/runs/run-pdf/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="run-pdf.pdf"' in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")


def test_the_downloads_are_gated_by_the_token_like_everything_else(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-locked"})
    client.runner.get("run-locked").state.update(REPORTED_STATE)
    client.headers.pop("X-API-Token")

    assert client.get("/api/runs/run-locked/report.tex").status_code == 401
    assert client.get("/api/runs/run-locked/report.pdf").status_code == 401


def test_a_run_with_no_report_says_so_for_every_format(client):
    """One refusal for all three downloads, not a crash from somewhere inside a renderer."""
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-bare"})

    assert client.get("/api/runs/run-bare/report.tex").status_code == 409
    assert client.get("/api/runs/run-bare/report.pdf").status_code == 409


def test_a_download_of_an_unknown_run_is_a_404(client):
    assert client.get("/api/runs/run-nope/report.pdf").status_code == 404


def test_health_needs_no_token_because_it_answers_nothing(client):
    client.headers.pop("X-API-Token")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_a_request_without_the_token_is_refused(client):
    client.headers.pop("X-API-Token")

    assert client.get("/api/runs").status_code == 401


def test_a_request_with_the_wrong_token_is_refused(client):
    client.headers["X-API-Token"] = "not-the-token"

    assert client.get("/api/runs").status_code == 401


def test_uploading_bids_starts_a_run_that_goes_on_to_the_gate(client):
    response = client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-upload"})

    assert response.status_code == 202
    body = response.json()
    assert body["run_id"] == "run-upload"
    assert body["uploaded"] == 1
    assert body["steps"] == list(models.STEPS)
    assert client.get("/api/runs/run-upload").json()["status"] == "awaiting_approval"


def test_an_uploaded_bid_is_written_under_its_own_run_folder(client):
    """Each run keeps its own folder, so two runs cannot read each other's documents and every
    path is inside settings.bids_dir, which is what parse_bid_pdf insists on."""
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-folder"})

    saved = list((settings.uploads_dir / "run-folder").glob("*.pdf"))
    assert [path.name for path in saved] == ["apex.pdf"]


def test_a_file_that_is_not_a_pdf_is_refused_by_name(client):
    response = client.post("/api/runs",
                           files=[("files", ("notes.txt", io.BytesIO(b"hello"), "text/plain"))],
                           data={"run_id": "run-bad"})

    assert response.status_code == 400
    assert "notes.txt" in response.json()["detail"]


def test_a_run_with_no_files_is_refused(client):
    response = client.post("/api/runs", data={"run_id": "run-empty"})

    assert response.status_code == 400


def test_the_run_id_is_the_servers_to_choose_not_the_callers(client):
    """A caller-supplied id becomes a checkpoint thread_id and a directory name, so a caller must
    not be able to put a path in it."""
    response = client.post("/api/runs", files=pdf_upload(),
                           data={"run_id": "../../etc/passwd"})

    assert response.status_code == 400
    assert "run_id" in response.json()["detail"]


def test_the_list_of_runs_is_empty_until_one_starts(client):
    assert client.get("/api/runs").json() == []


def test_a_snapshot_is_returned_for_a_run_that_exists(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-snapshot"})

    response = client.get("/api/runs/run-snapshot")

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-snapshot"
    assert len(response.json()["steps"]) == len(models.STEPS)


def test_an_unknown_run_is_refused_rather_than_invented(client):
    response = client.get("/api/runs/run-nope")

    assert response.status_code == 404
    assert "run-nope" in response.json()["detail"]


def test_the_raw_state_is_available_for_debugging(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-state"})

    response = client.get("/api/runs/run-state/state")

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-state"


def test_a_decision_needs_a_named_person(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-approve"})

    response = client.post("/api/runs/run-approve/approval",
                           json={"decision": "approved", "approved_by": "agent"})

    assert response.status_code == 422


def test_a_decision_that_the_approval_node_would_refuse_is_refused_here(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-bad-decision"})

    response = client.post("/api/runs/run-bad-decision/approval",
                           json={"decision": "maybe", "approved_by": "Grace Wanjiku"})

    assert response.status_code == 422


def test_deciding_on_a_run_that_is_not_waiting_is_a_conflict_not_a_crash(client):
    """The run has already ended, so there is nothing to decide and the answer says so."""
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-done"})
    client.runner.get("run-done").status = models.RunStatus.failed

    response = client.post("/api/runs/run-done/approval",
                           json={"decision": "approved", "approved_by": "Grace Wanjiku"})

    assert response.status_code == 409


def test_a_decision_on_a_run_waiting_at_the_gate_is_accepted(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-gated"})

    response = client.post("/api/runs/run-gated/approval",
                           json={"decision": "approved", "approved_by": "Grace Wanjiku"})

    assert response.status_code == 202
    assert response.json()["approved_by"] == "Grace Wanjiku"


def test_approving_a_report_with_no_evidence_is_refused_with_the_other_answer(thin_pool_client):
    """Plain approval of a report that rests on no benchmark is not allowed, and the person is told
    what to send instead. Refusing here rather than after the resume means the run is not driven
    into a failure by a click."""
    thin_pool_client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-thin"})

    response = thin_pool_client.post("/api/runs/run-thin/approval",
                                     json={"decision": "approved", "approved_by": "Grace Wanjiku"})

    assert response.status_code == 422
    assert "publish_anyway" in response.json()["detail"]
    assert "rejected" in response.json()["detail"]


def test_a_refused_decision_leaves_the_run_waiting_and_not_filed(thin_pool_client):
    """The point of checking first: the run is untouched, still paused, with nothing written."""
    thin_pool_client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-thin"})

    thin_pool_client.post("/api/runs/run-thin/approval",
                          json={"decision": "approved", "approved_by": "Grace Wanjiku"})

    run = thin_pool_client.runner.get("run-thin")
    assert run.status == models.RunStatus.awaiting_approval
    assert not run.busy
    assert run.state.get("submission") is None


def test_the_deliberate_override_is_accepted_for_a_report_with_no_evidence(thin_pool_client):
    thin_pool_client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-thin"})

    response = thin_pool_client.post("/api/runs/run-thin/approval",
                                     json={"decision": "publish_anyway",
                                           "approved_by": "Grace Wanjiku"})

    assert response.status_code == 202


def test_a_person_may_still_reject_a_report_with_no_evidence(thin_pool_client):
    """Refusing to publish is never blocked, however thin the evidence is."""
    thin_pool_client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-thin"})

    response = thin_pool_client.post("/api/runs/run-thin/approval",
                                     json={"decision": "rejected", "approved_by": "Grace Wanjiku"})

    assert response.status_code == 202


def test_the_report_is_served_as_the_markdown_the_agent_wrote(client):
    """Not reformatted for the browser: the file the committee reads is the file that is served."""
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-report"})
    client.runner.get("run-report").state["review_markdown"] = "# Price review evidence: T/1\n"

    response = client.get("/api/runs/run-report/report")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == "# Price review evidence: T/1\n"


def test_a_run_with_no_report_yet_says_so_instead_of_serving_nothing(client):
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-noreport"})

    response = client.get("/api/runs/run-noreport/report")

    assert response.status_code == 409


def test_the_browser_origin_is_allowed_and_others_are_not(client):
    allowed = client.options("/api/runs", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    refused = client.options("/api/runs", headers={
        "Origin": "https://somewhere-else.example", "Access-Control-Request-Method": "GET"})

    assert "http://localhost:5173" in allowed.headers.get("access-control-allow-origin", "")
    assert refused.headers.get("access-control-allow-origin") is None


def test_the_token_header_is_allowed_through_the_browser(client):
    """A preflight that did not allow it would leave the UI unable to approve anything."""
    response = client.options("/api/runs/run-approve/approval", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-token"})

    allowed_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "x-api-token" in allowed_headers


def test_a_server_with_no_token_refuses_to_start(monkeypatch, tmp_path):
    """An API that writes to the reports table must never come up open."""
    monkeypatch.setattr(settings, "api_token", "")

    with pytest.raises(Exception) as err:
        with TestClient(create_app(runner=Runner(PausedGraph()))) as unused:
            pass

    assert "API_TOKEN" in str(err.value)


def test_the_events_route_is_gated_by_the_token_like_everything_else(client):
    """The event stream is not an open window onto a tender: it carries the bids and the findings."""
    client.post("/api/runs", files=pdf_upload(), data={"run_id": "run-gated"})
    client.headers.pop("X-API-Token")

    assert client.get("/api/runs/run-gated/events").status_code == 401


def test_the_events_route_refuses_a_run_that_does_not_exist(client):
    assert client.get("/api/runs/run-nope/events").status_code == 404


def test_the_stream_sends_each_event_under_its_own_type(client):
    """The browser switches on the SSE event name, so the name has to be the model's own type."""

    async def scenario():
        channel = RunEventChannel()
        channel.emit(models.RunStarted(run_id="run-1", steps=list(models.STEPS), started_at="now"))
        channel.emit(models.ApprovalRequired(request=APPROVAL))
        channel.close()

        sent = [message async for message in _events(channel, lambda: 0.0)]

        assert [message["event"] for message in sent] == ["run.started", "approval.required"]
        assert '"run_id":"run-1"' in sent[0]["data"]

    asyncio.run(scenario())


def test_the_stream_keeps_talking_while_a_long_node_says_nothing():
    """quality_check can take forty seconds and reports nothing while it does. Without a heartbeat
    the browser cannot tell a slow step from a dead connection."""

    async def scenario():
        channel = RunEventChannel()
        channel.emit(models.StepStarted(step="quality_check", index=2, at="now"))

        stream = _events(channel, lambda: 41234.0, heartbeat=0.05)
        assert (await stream.__anext__())["event"] == "step.started"

        beat = await stream.__anext__()

        assert beat["event"] == "heartbeat"
        assert '"elapsed_ms":41234.0' in beat["data"]

    asyncio.run(scenario())


def test_the_stream_ends_when_the_run_ends(client):

    async def scenario():
        channel = RunEventChannel()
        channel.emit(models.RunFinished(status=models.RunStatus.filed, submission={"report_id": 7}))
        channel.close()

        stream = _events(channel, lambda: 0.0)
        first = await stream.__anext__()

        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        assert first["event"] == "run.finished"

    asyncio.run(scenario())
