import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { elapsed, KES, MONEY, PERCENT, positionWord, POSITION_WORDS } from "../lib/format";

/**
 * The house rules bind this interface too.
 *
 * agent/prompts/persona.md bans recommend, winner, should win, overpriced, gouging, rigging, rigged,
 * fraud and corrupt from any output, and mcp_server/tools/draft_review_file.py rejects a report that
 * uses one. An interface that described a bidder in those words would be making exactly the judgement
 * this project exists to prevent, so this reads the actual source rather than trusting review.
 */
const BANNED = [
  "recommend",
  "winner",
  "should win",
  "overpriced",
  "gouging",
  "rigging",
  "rigged",
  "fraud",
  "corrupt",
];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      return entry === "node_modules" ? [] : sourceFiles(path);
    }
    return /\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry) ? [path] : [];
  });
}

/** Comments are stripped first, so the rule is about what reaches the screen and not about a
 * developer being allowed to explain the rule in the source that enforces it. The stripping is
 * textual, so a banned word hidden inside a URL would be missed; there are none, and the copy in this
 * interface is all plain literals. */
function code(file: string): string {
  return readFileSync(file, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

describe("what this interface is allowed to say", () => {
  it("uses none of the banned words anywhere in the interface source", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(join(process.cwd(), "src"))) {
      const text = code(file).toLowerCase();
      for (const word of BANNED) {
        if (text.includes(word)) {
          offenders.push(`${file}: ${word}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  it("gives every position a word of its own, so nothing depends on colour", () => {
    const positions = Object.keys(POSITION_WORDS);
    const labels = new Set(positions.map((position) => positionWord(position).label));

    // The word is the carrier. The glyph beside it is a directional hint, not a second identifier:
    // above_range and far_above both point up, and it is the word that says which one this is.
    expect(labels.size).toBe(positions.length);
    for (const position of positions) {
      expect(positionWord(position).marker).toBeTruthy();
    }
  });

  it("names a position as a fact about a pool rather than a judgement about a bidder", () => {
    expect(positionWord("far_above").label).toBe("far above the median");
    expect(positionWord("far_above").marker).toBe("▲");
    expect(positionWord("within_range").label).toBe("inside the normal range");
  });

  it("says so plainly when there was not enough evidence to place a bid", () => {
    expect(positionWord(null).label).toBe("not enough evidence to place this bid");
    expect(positionWord(undefined).tone).toBe("thin");
  });

  it("never invents a word for a position it does not recognise", () => {
    expect(positionWord("invented_value").label).toBe("not recorded");
  });

  it("writes nothing as n/a rather than as zero", () => {
    expect(MONEY(null)).toBe("n/a");
    expect(PERCENT(null)).toBe("n/a");
    expect(KES(null)).toBe("n/a");
  });

  it("formats money the way a Kenyan tender document would", () => {
    expect(MONEY(2839845.72)).toBe("2,839,845.72");
    expect(KES(1228000)).toBe("KES 1,228,000.00");
  });

  it("keeps a percentage to one decimal, as the statistics tool already rounds it", () => {
    expect(PERCENT(206.7)).toBe("206.7%");
    expect(PERCENT(-2.5)).toBe("-2.5%");
  });

  it("shows a step's time in a way that does not jump about", () => {
    expect(elapsed(9400)).toBe("9.4s");
    expect(elapsed(64_200)).toBe("1m 04s");
    expect(elapsed(-5)).toBe("0.0s");
  });
});
