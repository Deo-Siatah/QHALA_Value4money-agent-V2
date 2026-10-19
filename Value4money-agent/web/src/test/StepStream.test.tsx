import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { Step, StepStatus } from "../api/types";
import type { StepCell } from "../api/events";
import { STEPS, STEP_WORDS } from "../api/types";
import { StepStream } from "../components/StepStream";

/**
 * Two views of one run.
 *
 * While the machine works, only the step that is happening now may fill the slot: a numbered badge,
 * the live detail, and on completion the badge turns into a tick before the slot is handed on. Once
 * the run pauses or ends, the same steps unfold into the vertical overview, each reopenable.
 *
 * State has to survive colour, because a committee prints this: a finished step carries a tick and a
 * word, not just green.
 */

function stream(overrides: Partial<Record<Step, Partial<StepCell>>> = {}) {
  return Object.fromEntries(
    STEPS.map((step) => [
      step,
      { status: "pending" as StepStatus, elapsedMs: 0, summary: "", ...(overrides[step] ?? {}) },
    ]),
  ) as Record<Step, StepCell>;
}

function renderStream(
  steps: Record<Step, StepCell>,
  currentStep: Step | null,
  phase: "processing" | "summary" = "processing",
) {
  return render(
    <StepStream
      steps={steps}
      currentStep={currentStep}
      phase={phase}
      body={(step) => <p>body for {step}</p>}
    />,
  );
}

describe("the step stream while processing", () => {
  it("holds one focal slot and shows only the running step's detail", () => {
    renderStream(stream({ research: { status: "running" } }), "research");

    expect(screen.getByText("body for research")).toBeInTheDocument();
    expect(screen.queryByText("body for score")).not.toBeInTheDocument();
    expect(screen.queryByText("1. Read the bids")).not.toBeInTheDocument();
  });

  it("badges the running step with its number", () => {
    renderStream(stream({ research: { status: "running" } }), "research");

    expect(screen.getByLabelText("step 2")).toBeInTheDocument();
    expect(screen.getByText("Step 2 of 8")).toBeInTheDocument();
  });

  it("turns the badge into a tick once the step is done", () => {
    const { container } = renderStream(stream({ research: { status: "done", elapsedMs: 7800, summary: "30 candidates" } }), "research");

    expect(screen.getByLabelText("step 2 complete")).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
    expect(screen.getByText("30 candidates")).toBeInTheDocument();
  });

  it("falls forward to the next unfinished step when none is marked running", () => {
    renderStream(stream({ intake: { status: "done", summary: "3 bids read" } }), null);

    expect(screen.getByText("body for research")).toBeInTheDocument();
    expect(screen.getByLabelText("step 2")).toBeInTheDocument();
  });
});

describe("the step stream as a summary", () => {
  it("unfolds all eight steps, so the shape of the whole job is visible", () => {
    renderStream(stream(), null, "summary");

    for (const step of STEPS) {
      expect(screen.getByText(new RegExp(STEP_WORDS[step].title))).toBeInTheDocument();
    }
  });

  it("keeps a finished step's own one-line summary", () => {
    renderStream(
      stream({ quality_check: { status: "done", elapsedMs: 32410, summary: "pool 17 · excluded 13" } }),
      null,
      "summary",
    );

    expect(screen.getByText("pool 17 · excluded 13")).toBeInTheDocument();
  });

  it("marks a finished step with a tick, in words as well as in a glyph", () => {
    const { container } = renderStream(
      stream({ intake: { status: "done", summary: "3 bids read" } }),
      null,
      "summary",
    );

    expect(screen.getByLabelText("intake: done")).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("leaves a step that has not run yet muted and shows its body to nobody", () => {
    renderStream(stream(), null, "summary");

    expect(screen.queryByText("body for approval")).not.toBeInTheDocument();
    expect(screen.getByText(STEP_WORDS.approval.subtitle)).toHaveClass("text-ink-400");
  });

  it("lets a finished step be opened again to read what it did", () => {
    renderStream(
      stream({ research: { status: "done", elapsedMs: 7800, summary: "30 candidates" } }),
      null,
      "summary",
    );

    fireEvent.click(screen.getByRole("button", { name: /find comparable awards/i }));

    expect(screen.getByText("body for research")).toBeInTheDocument();
  });

  it("shows the step's elapsed time beside it once it has one", () => {
    renderStream(stream({ research: { status: "done", elapsedMs: 7800 } }), null, "summary");

    expect(screen.getByText("7.8s")).toBeInTheDocument();
  });
});
