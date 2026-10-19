import { describe, expect, it } from "vitest";
import { staged, withoutFile, totalBytes } from "../lib/staging";

/**
 * Staging is the whole of "do not start until I say so". These are plain functions on purpose: the
 * rule that matters is that selecting files changes nothing about the run, and that is easier to
 * hold in a function than in a component.
 */

const pdf = (name: string, bytes = 1024) => new File([new Uint8Array(bytes)], name, { type: "application/pdf" });

describe("staging bids before anything is started", () => {
  it("holds the files that were chosen", () => {
    const files = staged([], [pdf("apex.pdf"), pdf("bora.pdf")]);

    expect(files.map((file) => file.name)).toEqual(["apex.pdf", "bora.pdf"]);
  });

  it("adds to what is already staged, because dropping twice in a row must not lose a bidder", () => {
    const files = staged([pdf("apex.pdf")], [pdf("bora.pdf")]);

    expect(files.map((file) => file.name)).toEqual(["apex.pdf", "bora.pdf"]);
  });

  it("never stages the same file twice", () => {
    const once = [pdf("apex.pdf")];

    expect(staged(once, once)).toHaveLength(1);
  });

  it("takes one file away without disturbing the rest", () => {
    const kept = staged([pdf("apex.pdf"), pdf("bora.pdf")], [pdf("zed.pdf")]);
    const removed = withoutFile(kept, "bora.pdf");

    expect(removed.map((file) => file.name)).toEqual(["apex.pdf", "zed.pdf"]);
  });

  it("takes one file away without taking a longer name that starts the same way", () => {
    const kept = [pdf("bora.pdf"), pdf("bora-contracts.pdf")];

    expect(withoutFile(kept, "bora.pdf").map((file) => file.name)).toEqual(["bora-contracts.pdf"]);
  });

  it("only ever holds PDFs, whatever the picker let through", () => {
    const files = staged([], [new File(["x"], "notes.txt", { type: "text/plain" })]);

    expect(files).toEqual([]);
  });

  it("says how much has been staged, in the units a person reads", () => {
    const files = staged([], [pdf("a.pdf", 1024 * 1024 * 2.4)]);

    expect(totalBytes(files)).toBe("2.4 MB");
  });

  it("calls an empty selection nothing at all rather than a round number", () => {
    expect(totalBytes([])).toBe("0 B");
  });
});