import { useId, useState } from "react";
import { KES, PERCENT, positionWord } from "../lib/format";
import type { BenchmarkView, BidView } from "../api/types";

/**
 * The comparison pool, drawn by hand.
 *
 * A chart library would have to be fought for a box plot, and this one has to print in black and
 * white: a committee file that only makes sense on a colour screen is not a committee file. So it is
 * a plain SVG — Q1 to Q3 as a box, the median as a line, min to max as whiskers, and each bid total
 * as a labelled marker on the same axis.
 *
 * Linear is the default because a committee should see real distances. Real awards run over orders of
 * magnitude, which flattens the interesting differences, so a log scale is one click away and the
 * control says which is showing. There is no third option that quietly changes the picture.
 */
export function PoolBoxPlot({
  poolValues,
  benchmark,
  bids,
}: {
  poolValues: number[];
  benchmark: BenchmarkView | null;
  bids: BidView[];
}) {
  const [scale, setScale] = useState<"linear" | "log">("linear");
  const gradientId = useId();

  if (poolValues.length === 0 || !benchmark) {
    return (
      <p className="rounded-md bg-ink-100 px-4 py-6 text-center text-sm text-thin">
        Not enough comparable awards to plot.
      </p>
    );
  }

  const sorted = [...poolValues].sort((a, b) => a - b);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  const median = benchmark.median_kes ?? sorted[Math.floor(sorted.length / 2)];
  const q1 = benchmark.q1_kes ?? sorted[Math.floor(sorted.length / 4)];
  const q3 = benchmark.q3_kes ?? sorted[Math.floor((sorted.length * 3) / 4)];

  const project = (value: number) => {
    if (scale === "linear") {
      return (value - min) / Math.max(1, max - min);
    }
    const low = Math.log10(Math.max(1, min));
    const high = Math.log10(Math.max(10, max));
    return (Math.log10(Math.max(1, value)) - low) / Math.max(0.0001, high - low);
  };

  const left = 8;
  const width = 100;
  const rowHeight = 30;
  const height = rowHeight * bids.length + 46;
  const x = (value: number) => left + project(value) * width;

  return (
    <figure className="space-y-3">
      <figcaption className="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-500">
        <span>
          {sorted.length} comparable awards · median {KES(median)} · normal range {KES(q1)} to {KES(q3)}
        </span>
        <button
          type="button"
          onClick={() => setScale(scale === "linear" ? "log" : "linear")}
          className="rounded border border-ink-300 px-2 py-0.5 font-medium text-ink-700 hover:bg-ink-100"
        >
          {scale === "linear" ? "Show on a log scale" : "Show on a linear scale"}
        </button>
      </figcaption>

      <svg viewBox={`0 0 ${width + left * 2} ${height}`} className="w-full" role="img"
        aria-label={`Box plot of ${sorted.length} comparable award values against the bid totals`}>
        <defs>
          <linearGradient id={gradientId} x1="0" x2="1">
            <stop offset="0%" stopColor="#e4e4e7" />
            <stop offset="100%" stopColor="#d4d4d8" />
          </linearGradient>
        </defs>

        <line x1={x(min)} y1={24} x2={x(max)} y2={24} stroke="#a1a1aa" strokeWidth={1} />
        <line x1={x(min)} y1={19} x2={x(min)} y2={29} stroke="#a1a1aa" strokeWidth={1} />
        <line x1={x(max)} y1={19} x2={x(max)} y2={29} stroke="#a1a1aa" strokeWidth={1} />
        <rect
          x={x(q1)}
          y={17}
          width={Math.max(0.5, x(q3) - x(q1))}
          height={14}
          fill={`url(#${gradientId})`}
          stroke="#a1a1aa"
        />
        <line x1={x(median)} y1={15} x2={x(median)} y2={33} stroke="#18181b" strokeWidth={2} />

        {bids.map((bid, index) => {
          const top = 38 + index * rowHeight;
          const position = positionWord(bid.position);
          const mark = position.tone === "thin" ? "#64748b" : "#18181b";
          return (
            <g key={bid.bidder}>
              <line x1={x(bid.total ?? 0)} y1={top - 8} x2={x(bid.total ?? 0)} y2={top + 8} stroke={mark} strokeWidth={2} />
              <text x={left} y={top + 4} fontSize={4} fill="#3f3f46">
                {bid.bidder.length > 26 ? `${bid.bidder.slice(0, 25)}…` : bid.bidder}
              </text>
              <text x={left + width} y={top + 4} fontSize={4} fill="#71717a" textAnchor="end">
                {KES(bid.total)} · {PERCENT(bid.pct_vs_median)}
              </text>
            </g>
          );
        })}
      </svg>

      <p className="text-xs text-ink-400">
        The box is the middle half of the pool, the line is the median, and the whiskers are its
        smallest and largest award. Each marker is one bid total. The scale is {scale}.
      </p>
    </figure>
  );
}
