# Run the same bids more than once and report whether the agent keeps saying the same thing.
#
# The fence tender in sample_bids/ is the regression set: three known bids, one of them with a
# deliberate arithmetic mismatch, and a public award pool that can answer for them. In October 2025
# nine runs over those three bids produced medians from KES 1.94M to 2.84M, and one bid came out
# within_range in some runs and far_above in others. Nothing was wrong with the arithmetic: the
# opening search query was being written by the model, so each run drew a different pool.
#
# Run it with:  uv run python -m evals.stability --runs 5
# It is not part of pytest: it needs a live model and a live database, and it takes minutes.

import argparse
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_RUNS = 5
DEFAULT_BIDS = "sample_bids"

# A quarter was too loose to catch what it is here for. In October 2025 the medians ran from
# KES 1.94M to 2.84M, which is about 19% either side of the middle, and a bid sitting near the 50%
# line flipped position across runs. The band has to be tighter than that spread, or it passes the
# exact failure it exists to find.
TOLERANCE_PCT = 10.0

POSITION_OF_INTEREST = "Bora Contractors (K) Ltd"


def median_spread(medians: List[float]) -> Dict[str, Any]:
    """How far apart the medians were, as the widest gap between any two of them."""
    if not medians:
        return {"n": 0, "lowest": None, "highest": None, "spread_pct": None}
    lowest, highest = min(medians), max(medians)
    spread = ((highest - lowest) / lowest * 100.0) if lowest else None
    return {"n": len(medians), "lowest": lowest, "highest": highest,
            "spread_pct": round(spread, 1) if spread is not None else None}


def within_band(medians: List[float], tolerance_pct: float = TOLERANCE_PCT) -> bool:
    """Whether every median landed inside tolerance of the middle one.

    A spread is judged against the middle rather than the extremes, so one wild run does not get to
    define the band on its own. No medians at all is not steady: nothing was benchmarked, and saying
    otherwise would report an empty run as agreement.
    """
    if not medians:
        return False
    if len(medians) == 1:
        return True
    middle = statistics.median(medians)
    if not middle:
        return False
    return all(abs(value - middle) / middle * 100.0 <= tolerance_pct for value in medians)


def summarise(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn several runs into the numbers that decide whether the agent is steady."""
    medians = [r["median_kes"] for r in results if r.get("median_kes")]
    positions = {}
    for result in results:
        for bid in result.get("bids", []):
            positions.setdefault(bid["bidder"], set()).add(bid.get("position"))
    unstable = {bidder: sorted(str(p) for p in seen) for bidder, seen in positions.items()
                if len(seen) > 1}
    return {"runs": len(results), "medians": medians, "spread": median_spread(medians),
            "stable": within_band(medians), "unstable_bids": unstable,
            "pool_sizes": [r.get("pool_size") for r in results]}


def _run_once(bids_dir: str, run_id: str) -> Dict[str, Any]:
    """Run the agent to the approval gate once, and take what its benchmark said.

    The run is left paused on purpose: the benchmark is already decided by then, and no report is
    filed, so measuring does not fill the reports table with copies of the same file.
    """
    from agent.checkpointer import checkpointer
    from agent.graph import build_graph
    from agent.state import initial_state
    from agent.tracing import flush, init_tracing

    init_tracing(run_id)
    paths = [str(path.resolve()) for path in sorted(Path(bids_dir).glob("*.pdf"))]
    with checkpointer() as saver:
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": run_id}}
        for chunk in graph.stream(initial_state(run_id, paths,
                                                datetime.now(timezone.utc).isoformat()),
                                  config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                break
        state = graph.get_state(config).values
    flush()

    benchmark = state.get("benchmark", {})
    return {"run_id": run_id, "median_kes": benchmark.get("median_kes"),
            "n": benchmark.get("n"), "confidence": benchmark.get("confidence"),
            "pool_size": state.get("pool_size"), "bids": benchmark.get("bids", [])}


def main(argv: List[str] = None) -> int:
    """Run the same bids several times and say whether the answers agree."""
    parser = argparse.ArgumentParser(description="Check the agent repeats itself on the same bids.")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--bids-dir", default=DEFAULT_BIDS)
    args = parser.parse_args(argv)

    results = []
    for index in range(1, args.runs + 1):
        run_id = f"stability-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{index}"
        print(f"run {index} of {args.runs} ({run_id}) ...", flush=True)
        results.append(_run_once(args.bids_dir, run_id))

    report = summarise(results)
    print("")
    print(f"runs: {report['runs']}")
    print(f"medians: {', '.join(f'{m:,.2f}' for m in report['medians']) or 'none'}")
    print(f"spread: {report['spread']['spread_pct']}% (tolerance {TOLERANCE_PCT}%)")
    print(f"pool sizes: {report['pool_sizes']}")
    if report["unstable_bids"]:
        print("bids that changed position between runs:")
        for bidder, seen in report["unstable_bids"].items():
            print(f"  {bidder}: {', '.join(seen)}")
    print("")
    print("STEADY" if report["stable"] and not report["unstable_bids"]
          else "NOT STEADY: the same bids did not produce the same answer")
    return 0 if report["stable"] and not report["unstable_bids"] else 1


if __name__ == "__main__":
    sys.exit(main())