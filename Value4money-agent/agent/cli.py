# The terminal front end: one place to start a run, watch it work, and answer for it.
#
# Nothing here decides anything. It shows the load screen, streams the run as each node finishes,
# brings the approval request, reads your answer, and then shows the report that was filed.
# The graph, the nodes and the tools are exactly as they are; run.py stays the scripted way in.
#
# Step one is the load screen, so before anything starts you know what will run and against what.
# Step two streams the run: one line per node as it finishes, carrying the fact that matters, and
# the model's own words as they are written underneath the step that asked for them.
# Step three is the approval. It is the only place this program waits for a person, and it never
# answers on your behalf: type "a Your Name" to approve or "r Your Name" to decline. A placeholder
# such as "agent" is refused here, rather than by the tool three nodes later.
# Step four says what was written, and prints the whole evidence file in your terminal.

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import settings
from agent.checkpointer import checkpointer
from agent.graph import build_graph
from agent.nodes.approval import PUBLISH_ANYWAY
from agent.state import initial_state
from agent.tracing import flush, init_tracing, start_trace, traced

STEP_NAMES = ["intake", "research", "quality_check", "score", "draft", "verify_sources",
              "approval", "file"]

TITLE = "Value4money · evidence for a tender committee"

PLACEHOLDERS = {"agent", "ai", "system", "bot", "auto", "none", "n/a", "unknown"}


class TokenPrinter(BaseCallbackHandler):
    """Print the model's words as they arrive, dimmed, under the step that asked for them.

    It reacts to the model call itself, so no node has to know the terminal is watching.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.printing = False
        self.wrote_anything = False

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        self.printing = True
        self.wrote_anything = False
        self.console.print("    [dim]Hasibu:[/dim]", end=" ")

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if not self.printing:
            return
        self.console.print(Text(token, style="dim italic"), end="")
        self.wrote_anything = True

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        self.stop()

    def stop(self) -> None:
        """Finish any line left open, once a node is done."""
        self.printing = False
        if self.wrote_anything:
            self.console.print()


def _host() -> str:
    """The database host, with the credentials never printed."""
    parsed = urlparse(settings.database_url or "")
    return parsed.hostname or "not set"


def _on_off(flag: bool) -> str:
    return "[green]on[/green]" if flag else "[dim]off[/dim]"


def banner(console: Console, run_id: str, paths: List[str]) -> None:
    """Show what is about to run, and against what, before anything starts."""
    body = Text.from_markup(
        f"[bold]run {run_id}[/bold]\n"
        f"bids {len(paths)} file(s) from {settings.bids_dir}\n"
        "\n"
        f"model {settings.llm_model} via {settings.llm_provider}\n"
        f"database {_host()}\n"
        f"community MCP {_on_off(settings.community_mcp_enabled and bool(settings.community_mcp_database_url))}"
        f"{f' ({settings.community_mcp_access_mode})' if settings.community_mcp_enabled else ''}\n"
        f"tracing {_on_off(settings.opik_enabled and bool(settings.opik_api_key))}\n"
        "checkpointer ready"
    )
    console.print(Panel(body, title=TITLE, border_style="cyan", padding=(1, 2)))


def tender_line(console: Console, update: Dict[str, Any]) -> None:
    """After intake, name the tender and what was read from it."""
    body = Text()
    body.append(f"tender {update.get('tender_ref', 'unknown')}\n")
    for bid in update.get("bids", []):
        body.append(f"  {bid.get('bidder', 'unknown'):<28} "
                    f"KES {bid.get('stated_total', 0):>14,.2f}\n")
        if not bid.get("arithmetic_ok", True):
            body.append(Text("    stated total does not match its line items\n", style="red"))
    for issue in update.get("parse_issues", []):
        body.append(Text(f"  {issue}\n", style="yellow"))
    console.print(Panel(body, title="read", border_style="cyan", padding=(1, 2)))


def summarise(name: str, update: Dict[str, Any]) -> str:
    """One fact about what a node just did, read from what that node returned."""
    if name == "intake":
        return f"{len(update.get('bids', []))} bids read"
    if name == "research":
        return f"{len(update.get('candidates', []))} candidates"
    if name == "quality_check":
        excluded = sum(update.get("excluded", {}).values())
        return f"pool {update.get('pool_size', 0)} · excluded {excluded}"
    if name == "score":
        benchmark = update.get("benchmark", {})
        if benchmark.get("confidence") == "insufficient":
            return "not enough comparable awards"
        return (f"median {benchmark.get('median_kes', 0):,.2f} · "
                f"{benchmark.get('confidence')} confidence")
    if name == "draft":
        return f"{len(update.get('findings', []))} findings · {len(update.get('sources', []))} sources"
    if name == "verify_sources":
        failures = update.get("verify_failures", [])
        return "all checks cleared" if not failures else f"{len(failures)} checks did not clear"
    if name == "approval":
        return str(update.get("approval_status", ""))
    if name == "file":
        submission = update.get("submission")
        return f"report {submission['report_id']}" if submission else "nothing filed"
    return ""


def step(console: Console, index: int, name: str, elapsed: float, update: Dict[str, Any]) -> None:
    """One finished node: how far, which step, how long, and what it found."""
    console.print(f"  [green]✓[/green] {index}/{len(STEP_NAMES)}  "
                  f"[bold]{name:<16}[/bold] {elapsed:>5.1f}s   "
                  f"[dim]{summarise(name, update)}[/dim]")


def approval_panel(console: Console, request: Dict[str, Any]) -> None:
    """Show the approver everything needed to decide, and nothing that presses them."""
    benchmark = request["benchmark"]
    body = Text()
    if request.get("evidence_status") == "no_evidence":
        body.append("this report has no comparable awards, so it holds no evidence\n")
        body.append("  publishing it is still allowed, but it says nothing about value for money.\n\n")
    if benchmark["confidence"] == "insufficient":
        body.append("not enough comparable awards\n")
        body.append(f"  {benchmark['n']} survived the checks, so there is no benchmark to "
                    f"compare these bids against.\n\n")
    else:
        body.append(f"{benchmark['n']} comparable awards · {benchmark['confidence']} confidence\n")
        body.append(f"median KES {benchmark['median_kes']:,.2f} · normal range "
                    f"{benchmark['q1_kes']:,.2f} to {benchmark['q3_kes']:,.2f}\n\n")
    body.append(f"summary\n  {request['summary']}\n")
    if request.get("verify_failures"):
        body.append("\nchecks that did not clear\n")
        for failure in request["verify_failures"]:
            body.append(f"  - {failure}\n")

    table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
    table.add_column("bidder")
    table.add_column("total KES", justify="right")
    table.add_column("% vs median", justify="right")
    table.add_column("position")
    for bid in request.get("bids", []):
        position = (bid["position"] or "n/a").replace("_", " ")
        style = "red" if bid["position"] in ("far_above", "far_below") else "green"
        table.add_row(bid["bidder"], f"{bid['total']:,.2f}",
                      "n/a" if bid["pct_vs_median"] is None else f"{bid['pct_vs_median']}%",
                      f"[{style}]{position}[/{style}]")

    console.print(Panel(body, title=f"waiting for a decision · {request['tender_ref']}",
                        border_style="yellow", padding=(1, 2)))
    console.print(table)


def parse_decision(answer: str, has_evidence: bool = True) -> Tuple[str, str]:
    """Read 'a Name' or 'r Name' into a decision and the name of the person making it.

    When the report holds no benchmark at all, plain approval is refused: publishing a document with
    no evidence in it has to be a choice the person can see they are making, so they type 'p Name'.
    """
    text = (answer or "").strip()
    parts = text.split(None, 1)
    if not parts:
        raise ValueError("answer with 'a Your Name' to approve, or 'r Your Name' to reject")

    verb = parts[0].lower()
    name = parts[1].strip() if len(parts) > 1 else ""

    if not has_evidence and verb == "a":
        raise ValueError("this report has no comparable awards, so it holds no evidence. "
                         "type 'p Your Name' to publish it anyway, or 'r Your Name' to stop")
    if verb == "p" and has_evidence:
        raise ValueError("this report has evidence, so it can simply be approved: "
                         "answer with 'a Your Name'")
    if verb == "p":
        if len(name) < 2:
            raise ValueError("publishing without evidence needs the name of the person deciding")
        if name.lower() in PLACEHOLDERS:
            raise ValueError("that is not a person. This report needs a named human.")
        return "approved", name
    if verb not in ("a", "r"):
        raise ValueError("answer with 'a Your Name' to approve, or 'r Your Name' to reject")

    decision = "approved" if verb == "a" else "rejected"
    if len(name) < 2:
        raise ValueError("a decision needs the name of the person making it")
    if name.lower() in PLACEHOLDERS:
        raise ValueError("that is not a person. This report needs a named human.")
    return decision, name


def ask_decision(console: Console, has_evidence: bool = True) -> Dict[str, str]:
    """Ask for the decision and the name, refusing anything that is not a person."""
    if has_evidence:
        hint = "(a Deo Siatah, or r Deo Siatah to reject)"
    else:
        hint = "(p Your Name to publish it without evidence, or r Your Name to stop)"
    console.print(f"\n  [bold]Decision and your name[/bold] [dim]{hint}[/dim]")
    while True:
        try:
            decision, name = parse_decision(input("  > "), has_evidence=has_evidence)
        except ValueError as exc:
            console.print(f"  [red]{exc}[/red]")
            continue
        decision_payload = {"decision": decision, "approved_by": name, "note": ""}
        if not has_evidence and decision == "approved":
            decision_payload["note"] = "published without a benchmark: no comparable awards found"
        return decision_payload


def filed_panel(console: Console, result: Dict[str, Any], run_id: str) -> None:
    """Say exactly what was written, and by whom."""
    submission = result.get("submission")
    if not submission:
        console.print(Panel(Text(f"approval status: {result.get('approval_status', 'unknown')}"),
                            title=f"{run_id} · filed nothing", border_style="yellow",
                            padding=(1, 2)))
        return
    body = Text()
    body.append(f"report id     {submission['report_id']}\n")
    body.append(f"approved by   {submission['approved_by']}\n")
    body.append(f"filed at      {submission['submitted_at']}\n")
    body.append(f"written to    {submission['file_path']}")
    console.print(Panel(body, title=f"{run_id} · filed", border_style="green", padding=(1, 2)))


def show_report(console: Console, markdown: str) -> None:
    """Print the whole evidence file, so it is read the way the committee reads it."""
    console.print()
    try:
        console.print(Markdown(markdown))
    except Exception:
        console.print(Panel(Text(markdown), border_style="cyan", padding=(1, 2)))


def run_live(console: Console, graph: Any, state: Dict[str, Any], config: Dict[str, Any],
             tokens: Optional[TokenPrinter] = None) -> Optional[Dict[str, Any]]:
    """Stream the run, one line per finished node, and return the approval request when it pauses."""
    previous = time.monotonic()
    for chunk in graph.stream(state, config, stream_mode="updates"):
        interrupts = chunk.get("__interrupt__") if isinstance(chunk, dict) else None
        if interrupts:
            if tokens is not None:
                tokens.stop()
            return interrupts[0].value
        for name, update in chunk.items():
            if not isinstance(update, dict):
                continue
            if tokens is not None:
                tokens.stop()
            now = time.monotonic()
            step(console, STEP_NAMES.index(name) + 1 if name in STEP_NAMES else 0, name,
                 now - previous, update)
            previous = now
            if name == "intake":
                tender_line(console, update)
    return None


def use_utf8() -> None:
    """Let the marks and separators print on a Windows console, which defaults to cp1252.

    Without this, a checkmark or a middle dot raises UnicodeEncodeError mid-run.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def build_console(no_colour: bool) -> Console:
    """Colour only when the terminal can show it."""
    use_utf8()
    return Console(no_color=bool(no_colour), highlight=False, soft_wrap=False)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Read the command line: the same arguments as the scripted runner, plus --no-colour."""
    from agent.run import parse_args as parse_run_args

    args = parse_run_args(argv, extra_arguments=[("--no-colour", {"action": "store_true",
                                                               "help": "plain output, no colour"})])
    args.no_colour = args.no_colour or False
    return args


def set_bids_dir(bids_dir: str) -> Path:
    """Point the whole run at one bids folder, including the tools that refuse outside paths."""
    folder = Path(bids_dir)
    settings.bids_dir = folder
    return folder


def bid_paths(bids_dir: str, names: Optional[List[str]]) -> List[str]:
    """The absolute PDF paths to read, the ones named or every PDF in the directory."""
    directory = Path(bids_dir)
    chosen = names or sorted(path.name for path in directory.glob("*.pdf"))
    if not chosen:
        raise FileNotFoundError(f"no bid PDFs in {directory}: run "
                                "'uv run python -m loader.make_sample_bids' first")
    return [str((directory / name).resolve()) for name in chosen]


def main(argv: Optional[List[str]] = None) -> int:
    """Start a run, watch it work, and take the approval from the person at the keyboard."""
    args = parse_args(argv)
    console = build_console(args.no_colour)

    missing = [name for name, value in [("LLM_MODEL", settings.llm_model),
                                        ("DATABASE_URL", settings.database_url)] if not value]
    if missing:
        console.print(Panel(f"[red]cannot start:[/red] set {', '.join(missing)} in .env",
                            title=TITLE, border_style="red"))
        return 4

    if args.resume:
        console.print(Panel("[yellow]this screen approves in person, so it cannot resume a run that "
                            "paused in another[/yellow]\n\n"
                            f"run:  uv run python -m agent.run --resume {args.resume} "
                            f"--decision approved --approved-by 'Your Name'",
                            title=TITLE, border_style="yellow"))
        return 4

    run_id = args.run_id or "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    started_at = datetime.now(timezone.utc).isoformat()
    tokens = TokenPrinter(console)
    config = {"configurable": {"thread_id": run_id},
              "callbacks": [] if args.no_colour else [tokens]}

    try:
        bids_folder = set_bids_dir(args.bids_dir)
        paths = bid_paths(str(bids_folder), args.pdfs)
    except FileNotFoundError as exc:
        console.print(Panel(f"[red]cannot start:[/red] {exc}", title=TITLE, border_style="red"))
        return 4

# The root span is opened here rather than by a decorator, because a decorator runs at import
    # time, before init_tracing() has configured Opik, and would quietly produce no trace at all.
    init_tracing(run_id)
    with checkpointer() as saver:
        banner(console, run_id, paths)
        console.print()
        graph = build_graph(saver)

        def work() -> Dict[str, Any]:
            """The part of the run that belongs in the root span, up to and including filing."""
            start_trace(run_id)
            request = run_live(console, graph, initial_state(run_id, paths, started_at), config, tokens)
            if request is None:
                return {}
            approval_panel(console, request)
            evidence = request.get("evidence_status") != "no_evidence"
            decision = ask_decision(console, has_evidence=evidence)
            if not evidence and decision["decision"] == "approved":
                decision = {**decision, "decision": PUBLISH_ANYWAY}
            console.print(f"\n  [dim]{decision['approved_by']} {decision['decision']} this report"
                          f"[/dim]\n")
            return graph.invoke(Command(resume=decision), config)

        result = traced("general", "agent_run")(work)()

    filed_panel(console, result, run_id)
    submission = result.get("submission") or {}
    if submission.get("file_path") and Path(submission["file_path"]).exists():
        show_report(console, Path(submission["file_path"]).read_text(encoding="utf-8"))
    flush()
    return 0 if result.get("submission") else 4


if __name__ == "__main__":
    sys.exit(main())
