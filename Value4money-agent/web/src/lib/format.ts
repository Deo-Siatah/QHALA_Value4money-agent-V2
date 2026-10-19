// How numbers and states are written on screen.
//
// The words here are the interface's own, and they are bound by the same rules the model is: agent/
// prompts/persona.md bans recommend, winner, should win, overpriced, gouging, rigging, rigged,
// fraud and corrupt from any output, and an interface that described a bidder in those words would be
// making exactly the judgement this project exists to prevent. test/copy.test.ts reads this file and
// fails if any of them appear.
//
// A position is never a verdict. "far above the median" is a fact about where a number sits in a pool
// of comparable awards, and it is always paired with a glyph and a word so it survives greyscale.

export const MONEY = (value: number | null | undefined): string =>
  value === null || value === undefined ? "n/a" : value.toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const KES = (value: number | null | undefined): string =>
  value === null || value === undefined ? "n/a" : `KES ${MONEY(value)}`;

export const PERCENT = (value: number | null | undefined): string =>
  value === null || value === undefined ? "n/a" : `${value.toFixed(1)}%`;

/** A duration, short enough to sit beside a step without pushing the layout around. */
export function elapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) {
    return "0.0s";
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(1)}s`;
  }
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

export type PositionTone = "neutral" | "ask" | "ask-strong" | "thin";

export interface PositionWord {
  label: string;
  marker: string;
  tone: PositionTone;
}

export const POSITION_WORDS: Record<string, PositionWord> = {
  within_range: { label: "inside the normal range", marker: "●", tone: "neutral" },
  above_range: { label: "above the normal range", marker: "▲", tone: "ask" },
  below_range: { label: "below the normal range", marker: "▼", tone: "ask" },
  far_above: { label: "far above the median", marker: "▲", tone: "ask-strong" },
  far_below: { label: "far below the median", marker: "▼", tone: "ask-strong" },
};

export const NO_PLACEMENT: PositionWord = {
  label: "not enough evidence to place this bid",
  marker: "–",
  tone: "thin",
};

/** What to print for a position. An unrecognised value says so rather than showing the enum. */
export function positionWord(position: string | null | undefined): PositionWord {
  if (!position) {
    return NO_PLACEMENT;
  }
  return POSITION_WORDS[position] ?? { label: "not recorded", marker: "–", tone: "thin" };
}

/** What one exclusion reason reads as. The codes are the tool's; these are the words. */
export const EXCLUSION_WORDS: Record<string, string> = {
  different_work: "a different kind of work",
  suspect_excluded: "suspect, and excluded by the model",
  duplicate_row: "the same award twice",
  no_value: "no value in the record",
  non_kes: "not in Kenyan shillings",
  negative_value: "a value of zero or less",
  missing_award_id: "no award identifier",
  scale_mismatch: "a different size of job from the bids",
  off_scale: "a different size of job from the bids",
};

export const exclusionWord = (code: string): string => EXCLUSION_WORDS[code] ?? code.replace(/_/g, " ");
