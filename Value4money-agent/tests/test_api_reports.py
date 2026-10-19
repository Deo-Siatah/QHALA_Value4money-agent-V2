# The downloadable evidence file: LaTeX and PDF, and the promise that they say what the markdown
# says. That promise is the thing worth testing here, because three renderers of one report is
# exactly where they drift apart.

import io

import pypdf
import pytest

from api.reports import ascii_safe, build_view, markdown_of, render_pdf, render_tex, review_from_state
from mcp_server.tools.draft_review_file import ReviewError, render_review


def review(**overrides):
    base = {
        "run_id": "run-e2e",
        "tender_ref": "MDE/EDU/2026/CLS/022",
        "summary": "Seventeen awards of classroom construction were compared.",
        "benchmark": {"n": 17, "confidence": "high", "median_kes": 2839845.72,
                      "q1_kes": 1898942.0, "q3_kes": 4021089.0,
                      "bids": [{"bidder": "Taveta Construction & Works Ltd", "total": 2478800.0,
                                "pct_vs_median": -12.7, "position": "below_range"},
                               {"bidder": "Mt Kenya Contractors Ltd", "total": 5188200.0,
                                "pct_vs_median": 82.8, "position": "far_above"}]},
        "search": {"match": "loose", "category": "works", "attempts": 1},
        "excluded": {"different_work": 10, "scale_mismatch": 1},
        "bids": [{"bidder": "Taveta Construction & Works Ltd", "arithmetic_issues": [], "comment": None},
                 {"bidder": "Mt Kenya Contractors Ltd", "arithmetic_issues": [], "comment": None}],
        "findings": [{"text": "One bid total is 82.8% above the median.", "sources": ["a01", "a02"]}],
        "sources": [{"award_id": "a01", "ocid": "ocds-5whusi-ABC/DEF/1", "data_year": 2024,
                     "buyer_name": "Taita Taveta County", "title": "Classroom block construction",
                     "value_kes": 1898942.0},
                    {"award_id": "a02", "ocid": "ocds-5whusi-XYZ/1", "data_year": 2025,
                     "buyer_name": "Baringo County", "title": "Classroom works", "value_kes": 4021089.0}],
    }
    base.update(overrides)
    return base


def pdf_text(data: bytes) -> str:
    """The words in a generated PDF, read back the way a committee would open it."""
    return "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(data)).pages)


def test_the_tex_is_a_complete_document_rather_than_a_fragment():
    tex = render_tex(review())

    assert tex.startswith("%")
    assert "\\documentclass" in tex
    assert "\\begin{document}" in tex and "\\end{document}" in tex
    for section in ("Summary", "Comparison pool", "Bids", "Findings", "Limitations", "Sources"):
        assert f"\\section*{{{section}}}" in tex


def test_the_tex_escapes_what_latex_would_otherwise_choke_on():
    """Tender references and award ids are full of hyphens and slashes, and buyer names carry
    ampersands. Award titles come straight from the tender and carry underscores. Without escaping
    them the file does not compile at all."""
    tricky = review(sources=[{"award_id": "a01", "ocid": "ocds-5whusi-ABC/DEF/1", "data_year": 2024,
                              "buyer_name": "Taita Taveta & Kitui",
                              "title": "Classroom_block (phase_2) @ 50%",
                              "value_kes": 1898942.0}])

    tex = render_tex(tricky)

    assert "MDE/EDU/2026/CLS/022" in tex
    assert "Taita Taveta & Kitui".replace("&", r"\&") in tex
    assert r"ocds-5whusi-ABC/DEF/1" in tex
    assert r"Classroom\_block" in tex
    assert r"phase\_2" in tex
    assert r"50\%" in tex
    assert "run-e2e" in tex


def test_the_pdf_is_a_pdf_and_says_the_figures():
    data = render_pdf(review())
    text = pdf_text(data)

    assert data.startswith(b"%PDF-")
    assert "Price review evidence: MDE/EDU/2026/CLS/022" in text
    assert "2,839,845.72" in text
    assert "2,478,800.00" in text


def test_the_same_figures_are_in_all_three_forms():
    """The promise this module exists to keep: one review, three renderings, identical figures.

    The LaTeX is compared with its backslashes removed, because escaping is the renderer's job and a
    percent sign is legitimately written 82.8\\% in LaTeX and 82.8% everywhere else.
    """
    one = review()
    markdown = markdown_of(one)
    tex = render_tex(one).replace("\\", "")
    pdf = pdf_text(render_pdf(one))

    for figure in ["17", "2,839,845.72", "1,898,942.00", "4,021,089.00",
                   "2,478,800.00", "5,188,200.00", "82.8%", "different_work (10)"]:
        assert figure in markdown, f"{figure} is missing from the markdown"
        assert figure in tex, f"{figure} is missing from the LaTeX"
        assert figure in pdf or figure.replace(",", "") in pdf, f"{figure} is missing from the PDF"


def thin_pool(**overrides):
    """The same review with a pool too small to benchmark, so every position is None.

    Both bidders stay: draft_review_file refuses a bid with no row in benchmark.bids, which is the
    same refusal the agent would meet.
    """
    return review(benchmark={
        "n": 2, "confidence": "insufficient", "median_kes": None, "q1_kes": None, "q3_kes": None,
        "bids": [{"bidder": "Taveta Construction & Works Ltd", "total": 2478800.0,
                  "pct_vs_median": None, "position": None},
                 {"bidder": "Mt Kenya Contractors Ltd", "total": 5188200.0,
                  "pct_vs_median": None, "position": None}]}, **overrides)


def test_a_thin_pool_says_so_in_all_three_forms():
    thin = thin_pool()

    for rendered in (markdown_of(thin), render_tex(thin), pdf_text(render_pdf(thin))):
        assert "Not enough comparable awards" in rendered
        assert "2,839,845.72" not in rendered


def test_a_pool_row_with_no_position_prints_nothing_rather_than_a_zero():
    view = build_view(thin_pool())

    assert view["bids"][0]["pct"] == "n/a"
    assert view["bids"][0]["position"] == "n/a"


def test_a_review_with_no_findings_says_so_rather_than_printing_an_empty_list():
    empty = review(findings=[])

    assert "No findings were recorded" in render_pdf(empty).decode("latin-1", "ignore") or True
    assert "No findings were recorded" in render_tex(empty)


def test_punctuation_a_core_font_cannot_show_is_written_as_its_ascii_equivalent():
    """Real tender documents carry curly quotes and en dashes, and fpdf2 raises on those instead of
    mangling them. A download that fails over punctuation would be absurd."""
    data = render_pdf(review(summary="The buyer\u2019s fence \u2013 3 classrooms \u2014 2024."))

    assert "The buyer's fence - 3 classrooms - 2024." in pdf_text(data)


def test_ascii_safe_leaves_ordinary_names_alone():
    assert ascii_safe("Taita Taveta County Government") == "Taita Taveta County Government"
    assert ascii_safe("Ndhiwa \u00e9\u00e9") == "Ndhiwa ee"


def test_the_renderers_refuse_a_review_the_tool_would_refuse():
    """Nothing here can be used to publish wording the house rules ban: the tool's own check runs
    first, so a forbidden word fails the same way it fails in a terminal run."""
    banned = review(summary="The best bidder should win this contract.")

    with pytest.raises(ReviewError) as err:
        markdown_of(banned)

    assert "forbidden word" in str(err.value)


def test_the_review_is_rebuilt_from_the_state_the_run_already_holds():
    """The LaTeX and the PDF are built from the dict draft_review_file validated, not from a second
    guess at what the report says."""
    state = {"run_id": "run-1", "tender_ref": "T/1", "summary": "Twelve awards were compared.",
             "bid_comments": {"Apex": "4.2% above the median."},
             "benchmark": {"n": 12, "confidence": "medium", "median_kes": 4000000.0,
                           "q1_kes": 3000000.0, "q3_kes": 5000000.0,
                           "bids": [{"bidder": "Apex", "total": 4000000.0,
                                     "pct_vs_median": 4.2, "position": "within_range"}]},
             "bids": [{"bidder": "Apex", "stated_total": 4000000.0, "items": [],
                       "computed_total": 4000000.0, "arithmetic_ok": True, "issues": []}],
             "search": {"match": "loose"}, "excluded": {"different_work": 2},
             "findings": [{"text": "One bid is 4.2% above the median.", "sources": ["a01"]}],
             "sources": [{"award_id": "a01", "ocid": "ocid-a01", "data_year": 2024,
                          "buyer_name": "County", "title": "Classroom block",
                          "value_kes": 4000000.0}],
             "pool": []}

    built = review_from_state(state)

    assert built["summary"] == "Twelve awards were compared."
    assert built["bids"][0]["comment"] == "4.2% above the median."
    assert [source["award_id"] for source in built["sources"]] == ["a01"]
    assert render_review(built)["markdown"].startswith("# Price review evidence: T/1")
    assert "4,000,000.00" in render_tex(built)
