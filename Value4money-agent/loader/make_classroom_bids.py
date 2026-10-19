# Build five synthetic classroom bid PDFs, a third demonstration set beside the fence bids in
# sample_bids/ and the road bids in pdfs/. Nothing here is a real tender: every page says so in the
# words parse_bid looks for.
#
# The figures are chosen against the real Kenya OCDS classroom awards, which sit between about 1.2M
# and 4.2M KES with a median near 2.4M, so the demonstration shows a pool that is genuinely
# comparable. They also spread on purpose: one bid far above the middle, one far below, one inside
# the range and one whose line items do not add up to its stated total, because a review file that
# has never caught anything proves nothing.
#
# The five bids price one scope: a substructure, a reinforced concrete frame, roofing, plastering and
# steel windows and doors. Only the unit prices differ.
#
# Run it with: uv run python -m loader.make_classroom_bids
# It writes five PDFs into pdfs/, which are read with:
#   uv run python main.py --bids-dir pdfs --pdfs karuru-works.pdf taveta-construction.pdf
#       nyando-enterprises.pdf rift-valley-builders.pdf mt-kenya-contractors.pdf
# The road bids sit in the same folder under a different tender reference, so the five files are
# named rather than letting the folder be read whole.

from pathlib import Path
from typing import List

from .make_sample_bids import write_bid

TENDER_REF = "MDE/EDU/2026/CLS/022"
BIDS_DIR = Path("pdfs")

# Four classroom blocks: substructure, frame, roof, finishes and openings, priced five ways.
KARURU = [
    ("Construction of classroom block substructure", "item", 4, 175000),
    ("Construction of reinforced concrete wall and column frame", "square metre", 860, 180),
    ("Supply and fixing of aluminium roofing sheets", "square metre", 320, 520),
    ("Plastering, rendering and painting of classroom block", "square metre", 640, 120),
    ("Supply and fixing of steel windows and doors", "each", 20, 6500),
]

TAVETA = [
    ("Construction of classroom block substructure", "item", 4, 320000),
    ("Construction of reinforced concrete wall and column frame", "square metre", 860, 420),
    ("Supply and fixing of aluminium roofing sheets", "square metre", 320, 1250),
    ("Plastering, rendering and painting of classroom block", "square metre", 640, 340),
    ("Supply and fixing of steel windows and doors", "each", 20, 11000),
]

NYANDO = [
    ("Construction of classroom block substructure", "item", 4, 300000),
    ("Construction of reinforced concrete wall and column frame", "square metre", 860, 520),
    ("Supply and fixing of aluminium roofing sheets", "square metre", 320, 1420),
    ("Plastering, rendering and painting of classroom block", "square metre", 640, 425),
    ("Supply and fixing of steel windows and doors", "each", 20, 15500),
]

RIFT_VALLEY = [
    ("Construction of classroom block substructure", "item", 4, 360000),
    ("Construction of reinforced concrete wall and column frame", "square metre", 860, 620),
    ("Supply and fixing of aluminium roofing sheets", "square metre", 320, 1450),
    ("Plastering, rendering and painting of classroom block", "square metre", 640, 480),
    ("Supply and fixing of steel windows and doors", "each", 20, 13000),
]

MT_KENYA = [
    ("Construction of classroom block substructure", "item", 4, 540000),
    ("Construction of reinforced concrete wall and column frame", "square metre", 860, 1150),
    ("Supply and fixing of aluminium roofing sheets", "square metre", 320, 2350),
    ("Plastering, rendering and painting of classroom block", "square metre", 640, 980),
    ("Supply and fixing of steel windows and doors", "each", 20, 33000),
]

# Nyando's line items come to 2,683,600 against a stated 2,833,600, on purpose.
BIDS = [
    ("karuru-works.pdf", "Karuru Works Agency Ltd", KARURU, 1228000),
    ("taveta-construction.pdf", "Taveta Construction & Works Ltd", TAVETA, 2478800),
    ("nyando-enterprises.pdf", "Nyando Enterprises Ltd", NYANDO, 2833600),
    ("rift-valley-builders.pdf", "Rift Valley Builders Ltd", RIFT_VALLEY, 3004400),
    ("mt-kenya-contractors.pdf", "Mt Kenya Contractors Ltd", MT_KENYA, 5188200),
]


def write_classroom_bids(directory: Path = BIDS_DIR) -> List[Path]:
    """Write every classroom bid into the given folder and return the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, bidder, items, stated_total in BIDS:
        path = directory / name
        computed = sum(quantity * unit_price for _, _, quantity, unit_price in items)
        write_bid(path, bidder, items, stated_total, TENDER_REF)
        written.append(path)
        print(f"wrote {path} | stated {stated_total:,.2f} | line items {computed:,.2f}"
              f"{' | ARITHMETIC MISMATCH ON PURPOSE' if abs(computed - stated_total) > 0.01 else ''}")
    return written


def main() -> None:
    """Write the five classroom bid PDFs into pdfs/."""
    write_classroom_bids(BIDS_DIR)


if __name__ == "__main__":
    main()