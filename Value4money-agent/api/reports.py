# The downloadable forms of the evidence file: LaTeX and PDF.
#
# The agent already decided what the report says, in draft_review_file, and the markdown it renders
# is the report. These two are the same report in other shapes, for a committee that wants a PDF or
# wants the LaTeX to keep. Neither one decides anything and neither one adds a word.
#
# The single rule here is that the three cannot disagree. The figures are formatted by the tool's own
# _kes, the comparison pool lines are its own _pool_lines, the fixed notice and the three limitations
# are its own constants, and the review dict is rebuilt by the agent's own build_review from the
# state the run already holds. A number that appears in the markdown therefore appears in the PDF for
# the same reason, not because two implementations happened to agree. test_the_same_figures_are_in_all_three
# holds them together.
#
# What is not here is a LaTeX compiler. There is no pdflatex, xelatex or tectonic on this machine, so
# the .tex is written to be compiled elsewhere and the .pdf is produced in pure Python with fpdf2,
# which needs no engine and behaves the same on Windows.

import unicodedata
from pathlib import Path
from typing import Any, Dict, List

from fpdf import FPDF
from jinja2 import Environment, FileSystemLoader, select_autoescape

from agent.nodes.draft import build_review
from mcp_server.tools.draft_review_file import (LIMITATIONS, NOTICE, NOT_ENOUGH, _kes, _pool_lines,
                                                 render_review)

TEMPLATES = Path(__file__).resolve().parent / "templates"

# Core PDF fonts only speak latin-1. Real tender documents carry curly quotes, en dashes and the odd
# non-breaking hyphen, and fpdf2 raises on those rather than mangling them. Rather than fail a
# download over punctuation, the characters that have a well known ASCII equivalent are written as it
# and anything else is transliterated, so the number and the name around it survive.
_ASCII_EQUIVALENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "‑": "-", "−": "-",
    "…": "...", " ": " ", " ": " ", " ": " ",
    "×": "x", "°": " deg", "•": "-", "→": "->",
}


def ascii_safe(text: Any) -> str:
    """Write a string with only characters the core PDF fonts can show."""
    plain = "".join(_ASCII_EQUIVALENTS.get(char, char) for char in str(text))
    folded = unicodedata.normalize("NFKD", plain)
    return "".join(char for char in folded if not unicodedata.combining(char)).encode(
        "ascii", "ignore").decode("ascii")


def tex_escape(text: Any) -> str:
    """Escape the characters LaTeX treats as syntax, in a way that survives a table cell.

    Award ids and OCIDs are full of hyphens and slashes, and tender titles carry ampersands, so this
    is not a precaution: without it the file does not compile.
    """
    plain = str(text)
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
                    "^": r"\textasciicircum{}"}
    return "".join(replacements.get(char, char) for char in plain)


def review_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild the review dict the draft node assembled, from the state the run already holds.

    The wording is stored on the run: summary, one comment per bidder, the findings and the sources
    they cite. Reassembling it with the agent's own builder means the LaTeX and the PDF are built from
    exactly the dict draft_review_file validated, rather than from a second guess at what the report
    says.
    """
    answer = {"summary": state.get("summary", ""),
              "bid_comments": state.get("bid_comments") or {},
              "findings": state.get("findings") or []}
    return build_review(state, answer, list(state.get("sources") or []))


def build_view(review: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the LaTeX and the PDF need, as rows rather than as markdown.

    The comparison pool lines come straight from the tool, so the two files cannot word that section
    differently from the markdown. The bid and source rows are cells rather than markdown table rows,
    but their figures are formatted by the same _kes, which is what test_the_same_figures_are_in_all_three
    checks.
    """
    benchmark = review.get("benchmark") or {}
    scored = {row["bidder"]: row for row in benchmark.get("bids") or []}

    bids = []
    for note in review.get("bids") or []:
        row = scored.get(note["bidder"]) or {}
        position = row.get("position")
        bids.append({
            "bidder": note["bidder"],
            "total": _kes(row["total"]) if row.get("total") is not None else "n/a",
            "pct": "n/a" if position is None else f"{row['pct_vs_median']}%",
            "position": "n/a" if position is None else str(position),
            "comment": "; ".join(list(note.get("arithmetic_issues") or [])
                                 + ([note["comment"]] if note.get("comment") else [])) or "-",
        })

    years = [source["data_year"] for source in review.get("sources") or []
             if source.get("data_year") is not None]
    return {
        "run_id": review["run_id"],
        "tender_ref": review["tender_ref"],
        "notice": NOTICE,
        "summary": review.get("summary", ""),
        "pool_lines": [line[2:] for line in _pool_lines(benchmark, review.get("search") or {},
                                                        review.get("excluded") or {})],
        "not_enough": NOT_ENOUGH,
        "bids": bids,
        "findings": [{"text": finding.get("text", ""),
                      "sources": ", ".join(finding.get("sources") or [])}
                     for finding in review.get("findings") or []],
        "limitations": list(LIMITATIONS),
        "sources": [{"award_id": source.get("award_id", ""), "ocid": source.get("ocid") or "-",
                     "data_year": source.get("data_year") if source.get("data_year") is not None else "-",
                     "buyer_name": source.get("buyer_name") or "-", "title": source.get("title") or "-",
                     "value_kes": _kes(source["value_kes"]) if source.get("value_kes") is not None else "-"}
                    for source in review.get("sources") or []],
        "dataset": (f"Dataset: Kenya OCDS award records, data years {min(years)}-{max(years)}."
                    if years else ""),
    }


def _environment() -> Environment:
    """The Jinja environment, with the escaping filter the template uses on every field."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                      autoescape=select_autoescape(default=False, default_for_string=False),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["tex"] = tex_escape
    return env


def render_tex(review: Dict[str, Any]) -> str:
    """The evidence file as LaTeX source, for a committee that keeps it in a repository."""
    return _environment().get_template("report.tex.j2").render(**build_view(review))


def render_pdf(review: Dict[str, Any]) -> bytes:
    """The evidence file as a PDF, in pure Python, with the same figures as the markdown.

    Long source tables and long finding texts flow across pages rather than being clipped: an
    evidence file that silently dropped half its sources would be worse than one that did not print.
    """
    view = build_view(review)
    pdf = FPDF(orientation="landscape", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()

    def heading(text: str) -> None:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, text=ascii_safe(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)

    def line(text: str, style: str = "") -> None:
        pdf.set_font("Helvetica", style, 9)
        pdf.multi_cell(0, 5, ascii_safe(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)

    def table(headings: List[str], widths: List[float], rows: List[List[str]]) -> None:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(238, 238, 238)
        for heading, width in zip(headings, widths):
            pdf.cell(width, 6, text=ascii_safe(heading), border=1, fill=True)
        pdf.set_font("Helvetica", "", 8)
        pdf.ln()
        for row in rows:
            top = pdf.get_y()
            height = 6
            for cell, width in zip(row, widths):
                height = max(height, 5 * (1 + len(ascii_safe(str(cell))) // max(1, int(width / 1.9))))
            if pdf.get_y() + height > 180:
                pdf.add_page()
                top = pdf.get_y()
            pdf.set_xy(pdf.l_margin, top)
            for cell, width in zip(row, widths):
                pdf.cell(width, height, text=ascii_safe(str(cell)), border=1, align="L")
            pdf.ln()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, text=ascii_safe(f"Price review evidence: {view['tender_ref']}"),
             new_x="LMARGIN", new_y="NEXT")
    line(f"Run ID: {view['run_id']}")
    line(view["notice"], style="I")

    heading("Summary")
    line(view["summary"])

    heading("Comparison pool")
    for item in view["pool_lines"]:
        line(f"- {item}")

    heading("Bids")
    table(["Bidder", "Total (KES)", "% vs median", "Position", "Comment"],
          [70, 35, 25, 30, 110],
          [[bid["bidder"], bid["total"], bid["pct"], bid["position"], bid["comment"]]
           for bid in view["bids"]])

    heading("Findings")
    for index, finding in enumerate(view["findings"], start=1):
        line(f"{index}. {finding['text']}")
        line(f"   Sources: {finding['sources']}")
    if not view["findings"]:
        line("No findings were recorded for this review.")

    heading("Limitations")
    for item in view["limitations"]:
        line(f"- {item}")

    heading("Sources")
    table(["Award ID", "OCID", "Data year", "Buyer", "Title", "Value (KES)"],
          [40, 55, 20, 45, 75, 35],
          [[source["award_id"], source["ocid"], source["data_year"], source["buyer_name"],
            source["title"], source["value_kes"]] for source in view["sources"]])
    if view["dataset"]:
        line(view["dataset"], style="I")

    return bytes(pdf.output())


def markdown_of(review: Dict[str, Any]) -> str:
    """The markdown the agent itself renders, used only to hold the three formats together."""
    return render_review(review)["markdown"]
