# Start a run, or resume one that is waiting for a human.
#
# A start command reads the bid PDFs, searches real public awards, has the model judge them,
# benchmarks the totals, drafts the committee file, checks it, and then stops. It stops because a
# named human has to approve before anything is filed, and it stops across process boundaries: the
# checkpointer holds where it got to, so the approval can come an hour or a week later.
#
#   uv run python main.py --pdfs apex-fencing.pdf bora-contractors.pdf
#   uv run python main.py --resume run-20261014-224512 --decision approved --approved-by "Grace Wanjiku"
#
# Exit codes say what happened: 0 the report was filed, 2 the run is waiting for a decision,
# 3 a person declined, 4 nothing was filed and the reason is printed.

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph.types import Command

from config import settings
from agent.checkpointer import checkpointer
from agent.graph import build_graph
from agent.state import initial_state
from agent.tracing import flush, init_tracing, record_graph, start_trace, traced

FILED, PAUSED, DECLINED, NOT_FILED = 0, 2, 3, 4


def new_run_id() -> str:
    """A run id that sorts by time and is safe to use as a file name."""
    return "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def bid_paths(bids_dir: Path, names: Optional[List[str]]) -> List[str]:
    """The absolute paths of the PDFs to read: the ones named, or every PDF in the bids directory.

    They are absolute because parse_bid_pdf resolves a bare file name against the bids directory
    itself, and a path already carrying that directory would be looked up twice.
    """
    directory = Path(bids_dir)
    if names:
        return [str((directory / name).resolve()) for name in names]
    found = sorted(directory.glob("*.pdf"))
    if not found:
        raise FileNotFoundError(f"no bid PDFs found in {directory}: run "
                                "'uv run python -m loader.make_sample_bids' to create them")
    return [str(path.resolve()) for path in found]


def preflight() -> None:
    """Refuse to start before anything runs, naming what is missing."""
    missing = []
    if not settings.llm_model:
        missing.append("LLM_MODEL (the name of the model to use)")
    if not settings.database_url:
        missing.append("DATABASE_URL")
    if missing:
        raise SystemExit("cannot start: set " + ", ".join(missing) + " in .env")


def write_draft(markdown: str, run_id: str, outputs_dir: Path) -> Path:
    """Put the unapproved draft where a person can read it, outside outputs/reports."""
    path = Path(outputs_dir) / "drafts" / f"{run_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


def _line(label: str, value: Any) -> str:
    return f"  {label:<22} {value}"


def print_paused(result: Dict[str, Any], run_id: str, draft_path: Path) -> None:
    """Show the approver what they are being asked about, and how to answer."""
    request = result["__interrupt__"][0].value
    benchmark = request["benchmark"]
    print(f"\n=== {run_id} is waiting for a decision ===")
    print(f"  tender{'':<18} {request['tender_ref']}")
    print(f"  summary{'':<17} {request['summary']}")
    print(_line("comparables (n)", benchmark["n"]))
    print(_line("confidence", benchmark["confidence"]))
    print(_line("median KES", benchmark["median_kes"]))
    print(_line("normal range", f"{benchmark['q1_kes']} to {benchmark['q3_kes']}"))
    for bid in request["bids"]:
        print(_line(f"bid {bid['bidder']}", f"{bid['total']} ({bid['pct_vs_median']}%, {bid['position']})"))
    if request["verify_failures"]:
        print("  checks that did not clear:")
        for failure in request["verify_failures"]:
            print(f"    - {failure}")
    print(f"\n  draft for reading: {draft_path}")
    print(f"\n  to approve:\n    uv run python main.py --resume {run_id} "
          f'--decision approved --approved-by "Your Name"')
    print(f'  to decline:\n    uv run python main.py --resume {run_id} '
          f'--decision rejected --approved-by "Your Name" --note "why"')


def print_outcome(result: Dict[str, Any], run_id: str) -> int:
    """Report what a finished run did, and what it refused to do."""
    submission = result.get("submission")
    if submission:
        print(f"\n=== {run_id} filed ===")
        print(_line("report id", submission["report_id"]))
        print(_line("approved by", submission["approved_by"]))
        print(_line("file", submission["file_path"]))
        print(_line("submitted at", submission["submitted_at"]))
        return FILED

    status = result.get("approval_status", "unknown")
    print(f"\n=== {run_id} filed nothing ===")
    print(_line("approval status", status))
    for error in result.get("errors", [])[-5:]:
        print(f"  - {error}")
    for failure in result.get("verify_failures", [])[-5:]:
        print(f"  - unresolved: {failure}")
    if status == "rejected":
        print(_line("approver", result.get("approved_by")))
        print(_line("note", result.get("approval_note")))
        return DECLINED
    return NOT_FILED


def start(args: argparse.Namespace) -> int:
    """Run from the bid PDFs to the approval gate, and stop there."""
    preflight()
    paths = bid_paths(Path(args.bids_dir), args.pdfs)
    run_id = args.run_id or new_run_id()
    started_at = datetime.now(timezone.utc).isoformat()
    start_trace(run_id)

    print(f"{run_id}: reading {len(paths)} bid file(s) from {args.bids_dir}")
    with checkpointer() as saver:
        graph = build_graph(saver)
        record_graph(graph)
        result = graph.invoke(
            initial_state(run_id, paths, started_at),
            {"configurable": {"thread_id": run_id}})

    if "__interrupt__" in result:
        draft_path = write_draft(result.get("review_markdown", ""), run_id, settings.outputs_dir)
        print_paused(result, run_id, draft_path)
        return PAUSED
    return print_outcome(result, run_id)


def resume(args: argparse.Namespace) -> int:
    """Continue a paused run with a decision from a named human."""
    preflight()
    run_id = args.resume
    decision = {"decision": args.decision, "approved_by": args.approved_by, "note": args.note or ""}
    config = {"configurable": {"thread_id": run_id}}
    start_trace(run_id)

    print(f"{run_id}: resuming with decision '{args.decision}' from {args.approved_by}")
    with checkpointer() as saver:
        if not _known_run(saver, config, run_id):
            print(f"there is no run '{run_id}' waiting to be resumed: it never started, or its "
                  "checkpoints were deleted. Start one with: uv run python main.py",
                  file=sys.stderr)
            return NOT_FILED
        graph = build_graph(saver)
        record_graph(graph)
        result = graph.invoke(Command(resume=decision), config)
    return print_outcome(result, run_id)


def _known_run(saver, config: Dict[str, Any], run_id: str) -> bool:
    """Whether the checkpointer holds a run under this id.

    A checkpointer answers with an empty checkpoint for a thread it has never seen, so the test is
    not that something came back but that what came back is this run.
    """
    checkpoint = saver.get_tuple(config)
    if checkpoint is None:
        return False
    return (checkpoint.checkpoint.get("channel_values") or {}).get("run_id") == run_id


def parse_args(argv: Optional[List[str]] = None,
               extra_arguments: Optional[List[tuple]] = None) -> argparse.Namespace:
    """Read the command line. The interactive CLI adds its own flags through extra_arguments."""
    parser = argparse.ArgumentParser(description="Compare bid prices with past public awards.")
    parser.add_argument("--pdfs", nargs="+", help="bid PDF file names inside the bids directory")
    parser.add_argument("--bids-dir", default=str(settings.bids_dir), help="where the bid PDFs live")
    parser.add_argument("--run-id", help="use this run id instead of a generated one")
    parser.add_argument("--resume", metavar="RUN_ID", help="continue a run that is waiting for a decision")
    parser.add_argument("--decision", choices=["approved", "rejected"], help="the human's decision")
    parser.add_argument("--approved-by", help="the name of the human deciding")
    parser.add_argument("--note", help="a note from the human deciding")
    for flag, options in extra_arguments or []:
        parser.add_argument(flag, **options)
    args = parser.parse_args(argv)

    if args.resume:
        if not args.decision or not args.approved_by:
            parser.error("--resume needs --decision and --approved-by")
    elif args.decision or args.approved_by:
        parser.error("--decision and --approved-by only make sense with --resume")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    """Run the agent, or continue a paused one, and say what happened."""
    args = parse_args(argv)
    run_id = args.resume or args.run_id
    init_tracing(run_id)
    # The root span is wrapped here rather than with a decorator, because a decorator would run at
    # import time, long before Opik was configured, and would quietly do nothing.
    runner = traced("general", "agent_run")(resume if args.resume else start)
    try:
        code = runner(args)
    except FileNotFoundError as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return NOT_FILED
    finally:
        # The thread id has to be written from inside the tracked function, while the root trace is
        # open: before it there is no trace to update, and after it the trace has already closed.
        # The SDK batches in the background and this process ends immediately afterwards, so
        # anything still queued would be lost without the flush.
        flush()
    return code


if __name__ == "__main__":
    sys.exit(main())