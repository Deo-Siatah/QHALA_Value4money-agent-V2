// Turning a stream of events into the run page.
//
// This is the part of the frontend with real logic in it, so it is a pure function over a plain
// object rather than a pile of setState calls. That way a whole run can be replayed in a test, and
// two of the awkward truths are handled once instead of in five components:
//
//   A slow step is silent. quality_check can run forty seconds and say nothing at all, because
//   LangGraph only reports a node when it finishes. So the step's *start* comes from step.started and
//   the clock is driven by our own heartbeat plus a local tick, rather than by the last thing that
//   happened to arrive.
//
//   A refresh must not lose the run. A snapshot from the server carries the same truth as the events
//   do, so the two are merged rather than one replacing the other.

import { STEPS, type ApprovalRequest, type RunEvent, type RunSnapshot, type RunStatus, type Step, type StepStatus } from "./types";

/** Keep the model's words bounded. A draft is short, but a slow browser could otherwise hold a lot. */
export const MAX_TOKEN_CHARS = 4000;

export interface StepCell {
  status: StepStatus;
  elapsedMs: number;
  summary: string;
}

export interface RunView {
  runId: string;
  status: RunStatus;
  steps: Record<Step, StepCell>;
  currentStep: Step | null;
  tokens: string;
  /** Open while Hasibu is writing, collapsed once it has finished, so the page settles down. */
  tokensOpen: boolean;
  approval: ApprovalRequest | null;
  errors: { step: Step | null; message: string }[];
  submission: Record<string, unknown> | null;
  elapsedMs: number;
  heartbeats: number;
}

export type Action =
  | { kind: "event"; event: RunEvent }
  | { kind: "snapshot"; snapshot: RunSnapshot }
  | { kind: "tick"; ms: number };

const PENDING: StepCell = { status: "pending", elapsedMs: 0, summary: "" };

function blankSteps(): Record<Step, StepCell> {
  return Object.fromEntries(STEPS.map((step) => [step, { ...PENDING }])) as Record<Step, StepCell>;
}

export function initialView(runId: string): RunView {
  return {
    runId,
    status: "queued",
    steps: blankSteps(),
    currentStep: null,
    tokens: "",
    tokensOpen: false,
    approval: null,
    errors: [],
    submission: null,
    elapsedMs: 0,
    heartbeats: 0,
  };
}

function withStep(view: RunView, step: Step, cell: Partial<StepCell>): RunView {
  const previous = view.steps[step] ?? PENDING;
  return {
    ...view,
    steps: { ...view.steps, [step]: { ...previous, ...cell } },
  };
}

function isStep(name: string): name is Step {
  return (STEPS as readonly string[]).includes(name);
}

/** A snapshot and the stream overlap, so the same error can arrive twice. Showing a committee the
 * same line twice reads as two problems when it is one. */
function mergeErrors(
  seen: { step: Step | null; message: string }[],
  incoming: { step: Step | null; message: string }[],
) {
  const merged = [...seen];
  for (const error of incoming) {
    if (!merged.some((existing) => existing.message === error.message)) {
      merged.push(error);
    }
  }
  return merged;
}

function fromSnapshot(view: RunView, snapshot: RunSnapshot): RunView {
  const steps = blankSteps();
  for (const timing of snapshot.steps ?? []) {
    if (!isStep(timing.step)) {
      continue;
    }
    steps[timing.step] = {
      status: timing.status,
      elapsedMs: timing.elapsed_ms ?? 0,
      summary: timing.summary ?? "",
    };
  }
  return {
    ...view,
    runId: snapshot.run_id,
    status: snapshot.status,
    steps,
    currentStep: snapshot.current_step && isStep(snapshot.current_step) ? snapshot.current_step : null,
    elapsedMs: snapshot.elapsed_ms ?? 0,
    submission: snapshot.submission ?? null,
    errors: mergeErrors(view.errors, (snapshot.errors ?? []).map((message) => ({ step: null, message }))),
  };
}

function fromEvent(view: RunView, event: RunEvent): RunView {
  switch (event.type) {
    case "run.started":
      return { ...view, runId: event.run_id, status: "running", elapsedMs: 0 };

    case "step.started":
      if (!isStep(event.step)) {
        return view;
      }
      return {
        ...withStep(view, event.step, { status: "running" }),
        status: view.status === "queued" ? "running" : view.status,
        currentStep: event.step,
      };

    case "step.finished": {
      if (!isStep(event.step)) {
        return view;
      }
      return {
        ...withStep(view, event.step, {
          status: "done",
          elapsedMs: event.elapsed_ms ?? 0,
          summary: event.summary ?? "",
        }),
        currentStep: null,
      };
    }

    case "model.token": {
      const tokens = (view.tokens + event.delta).slice(-MAX_TOKEN_CHARS);
      return { ...view, tokens, tokensOpen: true };
    }

    case "model.done":
      return { ...view, tokensOpen: false };

    case "approval.required":
      return {
        ...view,
        status: "awaiting_approval",
        approval: event.request,
        currentStep: "approval",
        tokensOpen: false,
      };

    case "run.finished":
      return {
        ...view,
        status: event.status,
        submission: event.submission ?? null,
        tokensOpen: false,
      };

    case "run.error":
      return { ...view, errors: [...view.errors, { step: event.step ?? null, message: event.message }] };

    case "heartbeat":
      return { ...view, elapsedMs: event.elapsed_ms ?? view.elapsedMs, heartbeats: view.heartbeats + 1 };

    default:
      return view;
  }
}

export function reduce(view: RunView, action: Action): RunView {
  if (action.kind === "event") {
    return fromEvent(view, action.event);
  }
  if (action.kind === "snapshot") {
    return fromSnapshot(view, action.snapshot);
  }
  // The clock runs while there is something to wait for, and stops once the run has ended, so a
  // finished run does not sit there counting up for ever.
  if (view.status === "running" || view.status === "queued" || view.status === "awaiting_approval") {
    return { ...view, elapsedMs: view.elapsedMs + action.ms };
  }
  return view;
}
