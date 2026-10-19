import { describe, expect, it } from "vitest";
import { initialView, MAX_TOKEN_CHARS, reduce, type Action, type RunView } from "../api/events";
import { REPLAY } from "../api/replay";
import type { RunEvent, RunSnapshot } from "../api/types";
import { STEPS } from "../api/types";

function play(events: RunEvent[], from: RunView = initialView("run-1")): RunView {
  return events.reduce((view, event) => reduce(view, { kind: "event", event } as Action), from);
}

describe("the reducer, against a recorded run", () => {
  it("plays the whole recorded sequence without losing a step", () => {
    const view = play(REPLAY);

    expect(view.runId).toBe("run-replay");
    expect(view.status).toBe("awaiting_approval");
    const done = STEPS.filter((step) => view.steps[step].status === "done");
    expect(done).toEqual(["intake", "research", "quality_check", "score", "draft", "verify_sources"]);
  });

  it("keeps each step's own time", () => {
    const view = play(REPLAY);

    expect(view.steps.quality_check.elapsedMs).toBe(32_410);
    expect(view.steps.research.elapsedMs).toBe(7_800);
    expect(view.steps.approval.status).toBe("pending");
  });

  it("shows the agent's own one-line summary under a finished step", () => {
    const view = play(REPLAY);

    expect(view.steps.quality_check.summary).toBe("pool 17 · excluded 13");
  });

  it("holds the approval request when the run pauses", () => {
    const view = play(REPLAY);

    expect(view.approval?.evidence_status).toBe("has_evidence");
    expect(view.approval?.benchmark.n).toBe(17);
    expect(view.currentStep).toBe("approval");
  });

  it("marks the step that is running and clears it when the step finishes", () => {
    const started = play(REPLAY.slice(0, 2));
    expect(started.currentStep).toBe("intake");
    expect(started.steps.intake.status).toBe("running");

    // Through the step.finished for intake and no further, so nothing is in flight.
    const finished = play(REPLAY.slice(0, 6));
    expect(finished.currentStep).toBeNull();
    expect(finished.steps.intake.status).toBe("done");
  });

  it("keeps the clock moving while a step is silent", () => {
    const duringSlowStep = play(REPLAY.slice(0, 12));
    const before = duringSlowStep.elapsedMs;

    const ticked = reduce(duringSlowStep, { kind: "tick", ms: 1_000 });

    expect(ticked.elapsedMs).toBeGreaterThan(before);
  });

  it("stops the clock once the run has ended, so a filed run does not count up for ever", () => {
    const finished = play([
      ...REPLAY,
      { type: "run.finished", status: "filed", submission: { report_id: 7 } },
    ]);

    const ticked = reduce(finished, { kind: "tick", ms: 5_000 });

    expect(ticked.elapsedMs).toBe(finished.elapsedMs);
    expect(ticked.submission).toEqual({ report_id: 7 });
  });

  it("opens the model's words while they arrive and folds them away when it finishes", () => {
    const streaming = play([
      { type: "run.started", run_id: "r", steps: [], started_at: "" },
      { type: "step.started", step: "draft", index: 4, at: "" },
      { type: "model.token", step: "draft", delta: "Seventeen awards " },
      { type: "model.token", step: "draft", delta: "were compared." },
    ]);
    expect(streaming.tokens).toBe("Seventeen awards were compared.");
    expect(streaming.tokensOpen).toBe(true);

    const done = play([
      { type: "model.done", step: "draft", prompt: "ChatGroq", elapsed_ms: 10 },
    ], streaming);
    expect(done.tokensOpen).toBe(false);
    expect(done.tokens).toBe("Seventeen awards were compared.");
  });

  it("keeps the model's words bounded", () => {
    const many = Array.from({ length: 2_000 }, () => "x".repeat(10));
    const view = play([
      { type: "run.started", run_id: "r", steps: [], started_at: "" },
      ...many.map((delta) => ({ type: "model.token" as const, step: "draft" as const, delta })),
    ]);

    expect(view.tokens.length).toBeLessThanOrEqual(MAX_TOKEN_CHARS);
  });

  it("records a failure with the step it happened on and says nothing was filed", () => {
    const view = play([
      ...REPLAY,
      { type: "run.error", step: "research", message: "RuntimeError: the database is away" },
      { type: "run.finished", status: "failed" },
    ]);

    expect(view.status).toBe("failed");
    expect(view.errors).toEqual([{ step: "research", message: "RuntimeError: the database is away" }]);
    expect(view.submission).toBeNull();
  });

  it("ignores a step it does not recognise rather than inventing one", () => {
    const view = play([{ type: "step.started", step: "invented" as never, index: 99, at: "" }]);

    expect(Object.keys(view.steps)).toEqual([...STEPS]);
  });

  it("survives seeing the same step twice, as a reconnecting stream will deliver it", () => {
    const once = play(REPLAY.slice(0, 6));
    const twice = play(REPLAY.slice(0, 6), once);

    expect(twice.steps.intake).toEqual(once.steps.intake);
  });

  it("takes the step truth from a snapshot as well as from the events, so a reload is not a blank page", () => {
    const snapshot: RunSnapshot = {
      run_id: "run-1",
      status: "awaiting_approval",
      tender_ref: "MDE/EDU/2026/CLS/022",
      category: "works",
      description: "construction of classroom blocks",
      current_step: "approval",
      elapsed_ms: 41_200,
      steps: STEPS.map((step, index) => ({
        step,
        index,
        status: index < 6 ? "done" : index === 6 ? "running" : "pending",
        elapsed_ms: index < 6 ? 1000 : 0,
        summary: index === 2 ? "pool 17 · excluded 13" : "",
      })),
      bids: [],
      benchmark: null,
      pool_size: 17,
      pool_values: [1_000_000, 2_800_000],
      excluded: {},
      summary: "",
      bid_comments: {},
      findings: [],
      sources: [],
      verify_ok: true,
      verify_failures: [],
      errors: ["one thing went wrong"],
      parse_issues: [],
      submission: null,
    };

    const view = reduce(initialView("run-1"), { kind: "snapshot", snapshot });

    expect(view.status).toBe("awaiting_approval");
    expect(view.steps.quality_check.summary).toBe("pool 17 · excluded 13");
    expect(view.steps.approval.status).toBe("running");
    expect(view.elapsedMs).toBe(41_200);
    expect(view.errors.map((error) => error.message)).toContain("one thing went wrong");
  });

  it("does not double up the errors a snapshot and an event both reported", () => {
    // A snapshot and the stream legitimately overlap, so the same message may arrive twice. Showing it
    // twice is noise; showing it once is the truth.
    const snapshot = {
      run_id: "run-1",
      status: "failed",
      errors: ["model could not answer draft_review_wording"],
    } as unknown as RunSnapshot;

    const view = reduce(initialView("run-1"), { kind: "snapshot", snapshot });
    const repeated = reduce(view, { kind: "snapshot", snapshot });

    expect(repeated.errors.filter((error) => error.message === "model could not answer draft_review_wording"))
      .toHaveLength(1);
  });
});
