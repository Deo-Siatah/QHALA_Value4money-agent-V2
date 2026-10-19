from pathlib import Path

import pytest
from rich.console import Console

from agent import cli
from agent.cli import (TokenPrinter, ask_decision, banner, bid_paths, build_console, filed_panel,
                       parse_decision, approval_panel, run_live, step, tender_line)


def printed(fn, *args, **kwargs) -> str:
    """Run something that prints, and return what it printed."""
    import io

    buffer = io.StringIO()
    console = Console(no_color=True, width=100, file=buffer, force_terminal=False)
    fn(console, *args, **kwargs)
    return buffer.getvalue()


@pytest.mark.parametrize("answer, expected", [
    ("a Deo Siatah", ("approved", "Deo Siatah")),
    ("A  Grace Wanjiku ", ("approved", "Grace Wanjiku")),
    ("r Deo Siatah", ("rejected", "Deo Siatah")),
    ("approve Deo", None),
    ("x Deo", None),
    ("a", None),
    ("a agent", None),
    ("a AI", None),
    ("", None),
    (None, None),
])
def test_the_decision_line_is_parsed_or_refused(answer, expected):
    if expected is None:
        with pytest.raises(ValueError):
            parse_decision(answer)
    else:
        assert parse_decision(answer) == expected


def test_a_placeholder_is_refused_and_asked_again(monkeypatch):
    """'a agent' is not a person, so the prompt asks again instead of filing anything."""
    import io

    answers = iter(["a agent", "maybe", "a Deo Siatah"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    buffer = io.StringIO()
    console = Console(no_color=True, width=100, file=buffer, force_terminal=False)

    decision = ask_decision(console)

    assert decision == {"decision": "approved", "approved_by": "Deo Siatah", "note": ""}
    text = buffer.getvalue()
    assert "not a person" in text
    assert "'a Your Name'" in text


def test_the_banner_names_the_run_and_the_settings(tmp_path):
    text = printed(banner, "run-1", [str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")])

    assert "run run-1" in text
    assert "bids 2 file(s)" in text
    assert "model" in text
    assert "checkpointer ready" in text
    assert "community MCP" in text and "tracing" in text


def test_the_banner_never_prints_the_database_credentials():
    text = printed(banner, "run-1", [])

    assert "npg_" not in text
    assert "://" not in text


def test_the_banner_shows_colour_marks_and_not_their_tags():
    """A panel built from Text must not leak the tags that style it."""
    text = printed(banner, "run-1", [])

    assert "[green]" not in text and "[/green]" not in text
    assert "[bold]" not in text


def test_the_chosen_bids_folder_is_the_one_the_tools_check_against(tmp_path):
    """parse_bid_pdf refuses a path outside settings.bids_dir, so --bids-dir must set it."""
    original = cli.settings.bids_dir
    try:
        cli.set_bids_dir(str(tmp_path))
        assert cli.settings.bids_dir == tmp_path
    finally:
        cli.settings.bids_dir = original


def test_a_bid_outside_the_settings_folder_is_still_refused(tmp_path):
    """The guard stays a guard: the fix widens the folder, it does not remove the check."""
    from mcp_server.tools.parse_bid_pdf import BidParseError, parse_bid
    from loader.make_road_bids import write_road_bids

    bids = tmp_path / "pdfs"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    write_road_bids(bids)
    stray = bids / "koromo-road-works.pdf"
    moved = outside / stray.name
    moved.write_bytes(stray.read_bytes())

    original = cli.settings.bids_dir
    try:
        cli.set_bids_dir(str(outside))
        with pytest.raises(BidParseError):
            parse_bid(stray, cli.settings.bids_dir)
        assert parse_bid(moved, cli.settings.bids_dir)["bidder"] == "Koromo Road Works Ltd"
    finally:
        cli.settings.bids_dir = original


def test_the_approval_panel_says_so_when_there_was_not_enough_evidence():
    """benchmark_price returns None figures when the pool is too thin, and the panel must not lie."""
    request = {"tender_ref": "T/1", "benchmark": {"n": 0, "confidence": "insufficient",
                                                   "median_kes": None, "q1_kes": None,
                                                   "q3_kes": None},
              "summary": "No comparable awards were found.",
              "bids": [{"bidder": "Koromo Road Works Ltd", "total": 3231600000.0,
                        "pct_vs_median": None, "position": None}]}

    text = printed(approval_panel, request)

    assert "not enough comparable awards" in text
    assert "n/a" in text
    assert "None" not in text


def test_a_step_line_carries_the_fact_that_matters():
    text = printed(step, 2, "research", 2.1,
                   {"candidates": [{"award_id": "a1"}] * 16})

    assert "2/8" in text
    assert "research" in text
    assert "2.1s" in text
    assert "16 candidates" in text


def test_a_step_line_says_when_there_was_not_enough_evidence():
    text = printed(step, 4, "score", 0.1,
                   {"benchmark": {"confidence": "insufficient"}})

    assert "not enough comparable awards" in text


def test_the_tender_line_names_the_bids_and_the_arithmetic_issue():
    update = {"tender_ref": "TENDER/NC/2026/014", "parse_issues": ["broken.pdf: no items"],
              "bids": [{"bidder": "Apex Fencing Contractors Ltd", "stated_total": 3150000.0,
                        "arithmetic_ok": False},
                       {"bidder": "Bora Contractors (K) Ltd", "stated_total": 8650000.0,
                        "arithmetic_ok": True}]}

    text = printed(tender_line, update)

    assert "TENDER/NC/2026/014" in text
    assert "3,150,000.00" in text
    assert "8,650,000.00" in text
    assert "broken.pdf" in text
    assert "line items" in text


def test_a_rejected_run_says_plainly_that_it_filed_nothing():
    text = printed(filed_panel, {"approval_status": "rejected", "submission": None}, "run-9")

    assert "filed nothing" in text
    assert "approval status: rejected" in text
    assert "report id" not in text


def test_a_filed_run_names_the_report_and_the_person():
    text = printed(filed_panel, {"submission": {"report_id": "3", "approved_by": "Deo Siatah",
                                                "submitted_at": "2026-10-15 10:33:36+00:00",
                                                "file_path": "outputs/reports/run-1.md"}}, "run-1")

    assert "report id     3" in text
    assert "Deo Siatah" in text
    assert "outputs/reports/run-1.md" in text


def test_the_approval_panel_reads_the_positions_in_english():
    request = {"tender_ref": "T/1", "benchmark": {"n": 4, "confidence": "high",
                                                   "median_kes": 2670667.86,
                                                   "q1_kes": 1736209.1, "q3_kes": 4124999.25},
              "summary": "Four similar contracts were compared.",
              "bids": [{"bidder": "Bora Contractors (K) Ltd", "total": 8650000.0,
                        "pct_vs_median": 223.9, "position": "far_above"},
                       {"bidder": "Apex Fencing", "total": 3150000.0,
                        "pct_vs_median": 17.9, "position": "within_range"}]}

    text = printed(approval_panel, request)

    assert "far above" in text and "within range" in text
    assert "far_above" not in text
    assert "223.9%" in text and "2,670,667.86" in text


def test_the_token_printer_writes_the_words_it_is_given():
    import io

    buffer = io.StringIO()
    console = Console(no_color=True, width=100, file=buffer, force_terminal=False)
    printer = TokenPrinter(console)

    printer.on_llm_start(None, ["prompt"])
    for token in ["Eighteen ", "awards ", "compared."]:
        printer.on_llm_new_token(token)
    printer.on_llm_end(None)

    assert "Hasibu" in buffer.getvalue()
    assert "Eighteen awards compared." in buffer.getvalue()


def test_the_token_printer_says_nothing_when_it_never_started():
    import io

    buffer = io.StringIO()
    printer = TokenPrinter(Console(no_color=True, width=100, file=buffer, force_terminal=False))

    printer.on_llm_new_token("nothing should appear")

    assert buffer.getvalue() == ""


def test_run_live_prints_a_line_per_node_and_returns_the_request(capsys, monkeypatch):
    import io

    class FakeGraph:
        def stream(self, state, config, stream_mode):
            yield {"intake": {"bids": [{"bidder": "Apex"}], "parse_issues": []}}
            yield {"score": {"benchmark": {"confidence": "high", "median_kes": 4800000.0}}}
            yield {"__interrupt__": (type("I", (), {"value": {"run_id": "run-1"}})(),)}

    buffer = io.StringIO()
    console = Console(no_color=True, width=100, file=buffer, force_terminal=False)

    request = run_live(console, FakeGraph(), {}, {})

    assert request == {"run_id": "run-1"}
    text = buffer.getvalue()
    assert "intake" in text and "score" in text
    assert "1 bids read" in text
    assert "median 4,800,000.00" in text


def test_bid_paths_are_absolute_and_default_to_every_pdf(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"")
    (tmp_path / "b.pdf").write_bytes(b"")
    named = bid_paths(str(tmp_path), ["b.pdf"])
    every = bid_paths(str(tmp_path), None)

    assert named == [str((tmp_path / "b.pdf").resolve())]
    assert [Path(path).name for path in every] == ["a.pdf", "b.pdf"]


def test_an_empty_bids_directory_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError) as err:
        bid_paths(str(tmp_path), None)

    assert "make_sample_bids" in str(err.value)


def test_help_lists_every_flag_the_cli_accepts(capsys):
    from agent.run import parse_args as parse_run_args

    with pytest.raises(SystemExit):
        cli.parse_args(["--help"])

    out = capsys.readouterr().out
    for flag in ["--bids-dir", "--pdfs", "--run-id", "--no-colour"]:
        assert flag in out


def test_the_no_colour_flag_is_read_from_the_command_line():
    assert cli.parse_args(["--no-colour"]).no_colour is True
    assert cli.parse_args([]).no_colour is False


def test_colour_is_off_when_asked():
    assert build_console(True).no_color is True


def test_the_output_is_switched_to_utf8_so_the_marks_print():
    """A Windows console defaults to cp1252, where a checkmark would otherwise raise."""
    class FakeStream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    stdout, stderr = FakeStream(), FakeStream()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(cli.sys, "stdout", stdout)
    monkey.setattr(cli.sys, "stderr", stderr)
    try:
        build_console(True)
    finally:
        monkey.undo()

    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_a_stream_without_reconfigure_is_not_fatal():
    """An old Python or an already-captured stream must not stop the run."""
    class Plain:
        pass

    monkey = pytest.MonkeyPatch()
    monkey.setattr(cli.sys, "stdout", Plain())
    monkey.setattr(cli.sys, "stderr", Plain())
    try:
        assert build_console(False).no_color is False
    finally:
        monkey.undo()


import io


def test_a_report_with_no_evidence_is_approved_only_by_a_different_word():
    """Typing the usual approval is refused when the report holds no benchmark at all."""
    with pytest.raises(ValueError) as err:
        parse_decision("a Deo Siatah", has_evidence=False)

    assert "no comparable awards" in str(err.value)
    assert "'p Your Name'" in str(err.value)


def test_the_publishing_word_is_refused_when_there_is_evidence():
    with pytest.raises(ValueError):
        parse_decision("p Deo Siatah", has_evidence=True)


def test_a_report_with_no_evidence_is_published_by_typing_p():
    assert parse_decision("p Deo Siatah", has_evidence=False) == ("approved", "Deo Siatah")


def test_a_report_with_no_evidence_can_still_be_declined_the_usual_way():
    assert parse_decision("r Deo Siatah", has_evidence=False) == ("rejected", "Deo Siatah")


def test_publishing_without_evidence_still_needs_a_name():
    with pytest.raises(ValueError):
        parse_decision("p", has_evidence=False)


def test_publishing_without_evidence_still_refuses_a_placeholder():
    with pytest.raises(ValueError):
        parse_decision("p agent", has_evidence=False)


def test_the_prompt_tells_the_person_what_the_two_words_mean(capsys, monkeypatch):
    answers = iter(["p Deo Siatah"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    console = Console(no_color=True, width=100, file=io.StringIO())

    decision = ask_decision(console, has_evidence=False)

    assert decision["decision"] == "approved"
    assert decision["approved_by"] == "Deo Siatah"
    assert decision["note"] == "published without a benchmark: no comparable awards found"


def test_the_prompt_offers_the_usual_words_when_there_is_evidence(capsys, monkeypatch):
    answers = iter(["a Deo Siatah"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    console = Console(no_color=True, width=100, file=io.StringIO())

    decision = ask_decision(console, has_evidence=True)

    assert decision["note"] == ""


def test_the_approval_panel_says_the_report_holds_no_evidence():
    request = {"tender_ref": "T/1", "evidence_status": "no_evidence",
               "benchmark": {"n": 0, "confidence": "insufficient", "median_kes": None,
                             "q1_kes": None, "q3_kes": None},
               "summary": "No comparable awards were found.",
               "bids": [{"bidder": "Koromo Road Works Ltd", "total": 3231600000.0,
                         "pct_vs_median": None, "position": None}]}

    text = printed(approval_panel, request)

    assert "holds no evidence" in text
    assert "nothing about value for money" in text


def test_a_report_with_evidence_does_not_carry_the_no_evidence_warning():
    request = {"tender_ref": "T/1", "evidence_status": "has_evidence",
               "benchmark": {"n": 17, "confidence": "high", "median_kes": 2670667.86,
                             "q1_kes": 1736209.1, "q3_kes": 4124999.25},
               "summary": "Seventeen awards compared.", "bids": []}

    text = printed(approval_panel, request)

    assert "holds no evidence" not in text
    assert "17 comparable awards" in text
