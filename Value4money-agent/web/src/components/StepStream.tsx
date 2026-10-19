import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Loader2 } from "lucide-react";
import type { Step } from "../api/types";
import { STEPS, STEP_WORDS } from "../api/types";
import type { StepCell } from "../api/events";
import { elapsed } from "../lib/format";
import { cn } from "../lib/utils";

/**
 * The run, in one place.
 *
 * While Hasibu works, the page must not be a stack of cards: a reader watching a review wants the
 * one thing that is happening now, not eight panels to scroll past. So processing uses a single
 * focal slot. A numbered badge names the step, the detail streams live inside it, and when the step
 * finishes the badge turns into a tick and the same slot is handed to the next step.
 *
 * Once the run reaches the human node (or ends), the slot unfolds into the whole job: every step,
 * its tick, its one-line summary, and a disclosure that reopens the detail for that step.
 *
 * The summaries come from agent/cli.py's own summarise(), so this column and the terminal describe a
 * step in exactly the same words. Nothing here invents a description.
 */

export type StreamPhase = "processing" | "summary";

const PENDING: StepCell = { status: "pending", elapsedMs: 0, summary: "" };

/** The step to hold in the focal slot: the running one, else the first not-yet-done one. */
function activeStepOf(steps: Record<Step, StepCell>, currentStep: Step | null): Step {
  if (currentStep) {
    return currentStep;
  }
  const next = STEPS.find((step) => (steps[step] ?? PENDING).status !== "done");
  return next ?? STEPS[STEPS.length - 1];
}

export function StepStream({
  steps,
  currentStep,
  body,
  phase = "processing",
  className,
}: {
  steps: Record<Step, StepCell>;
  currentStep: Step | null;
  body: (step: Step) => React.ReactNode;
  phase?: StreamPhase;
  className?: string;
}) {
  if (phase === "summary") {
    return (
      <ol className={cn("space-y-2", className)} aria-label="Progress">
        {STEPS.map((step) => (
          <SummaryRow
            key={step}
            step={step}
            cell={steps[step] ?? PENDING}
            open={currentStep === step}
          >
            {body(step)}
          </SummaryRow>
        ))}
      </ol>
    );
  }

  return (
    <FocalStep
      steps={steps}
      currentStep={currentStep}
      body={body}
      className={className}
    />
  );
}

/** One slot, one step at a time. Keyed on the step so the swap replays the entrance animation. */
function FocalStep({
  steps,
  currentStep,
  body,
  className,
}: {
  steps: Record<Step, StepCell>;
  currentStep: Step | null;
  body: (step: Step) => React.ReactNode;
  className?: string;
}) {
  const step = activeStepOf(steps, currentStep);
  const cell = steps[step] ?? PENDING;
  const index = STEPS.indexOf(step) + 1;
  const running = cell.status === "running";
  const done = cell.status === "done";

  return (
    <div className={cn("mx-auto w-full max-w-2xl", className)} aria-label="Progress">
      <div
        className="animate-slot-in overflow-hidden rounded-xl border border-ink-200 bg-paper shadow-lift"
        key={step}
      >
        <div className="flex flex-col items-center gap-3 border-b border-ink-200 px-5 py-6 text-center">
          <StepBadge index={index} running={running} done={done} />
          <div className="min-w-0">
            <p
              className={cn(
                "text-sm font-semibold",
                done ? "text-ink-900" : running ? "text-accent" : "text-ink-700",
              )}
            >
              {STEP_WORDS[step].title}
            </p>
            <p className="mt-0.5 text-xs text-ink-500">
              {done
                ? cell.summary || "Done"
                : running
                  ? STEP_WORDS[step].subtitle
                  : "Waiting to start"}
            </p>
          </div>
          <p className="text-[0.7rem] font-medium tracking-wide text-ink-400 uppercase">
            Step {index} of {STEPS.length}
          </p>
        </div>

        <div className="px-4 py-4 sm:px-5 sm:py-5">{body(step)}</div>
      </div>
    </div>
  );
}

/** The circular marker: a number while the step is to come or running, a tick once it is done. */
function StepBadge({ index, running, done }: { index: number; running: boolean; done: boolean }) {
  return (
    <span
      aria-label={done ? `step ${index} complete` : `step ${index}`}
      className={cn(
        "flex size-12 shrink-0 items-center justify-center rounded-full text-lg font-semibold transition-colors",
        done
          ? "bg-done text-paper"
          : running
            ? "animate-pulse-glow bg-accent text-paper"
            : "bg-ink-100 text-ink-500",
      )}
    >
      {done ? <Check className="animate-check-pop size-6" /> : index}
    </span>
  );
}

/** The final overview: every step, collapsible, with its own detail kept behind a disclosure. */
function SummaryRow({
  step,
  cell,
  open,
  children,
}: {
  step: Step;
  cell: StepCell;
  open: boolean;
  children: React.ReactNode;
}) {
  // A finished step stays closed until asked, but a step the run is sitting on is remembered so
  // toggling it does not lose the reader's place.
  const [pinned, setPinned] = useState(false);
  const words = STEP_WORDS[step];
  const index = STEPS.indexOf(step) + 1;
  const running = cell.status === "running";
  const done = cell.status === "done";
  const showBody = open || pinned;

  return (
    <li
      className={cn(
        "overflow-hidden rounded-lg border bg-paper transition-shadow",
        running ? "border-accent/40 shadow-lift" : done ? "border-ink-200 shadow-soft" : "border-ink-200",
      )}
    >
      <button
        type="button"
        onClick={() => (done || running) && setPinned(!pinned)}
        disabled={cell.status === "pending"}
        aria-expanded={showBody}
        className={cn(
          "flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left",
          (done || running) && "hover:bg-ink-100",
          cell.status === "pending" && "cursor-default",
        )}
      >
        <span aria-label={`${step}: ${cell.status}`} className="w-5 shrink-0">
          {running ? (
            <Loader2 className="size-4 animate-spin text-accent" />
          ) : done ? (
            <Check className="size-4 text-done" />
          ) : (
            <span className="block size-4 rounded-full border border-ink-300" />
          )}
        </span>

        <span className="flex min-w-0 flex-col">
          <span
            className={cn(
              "text-sm",
              done ? "font-medium text-ink-900" : running ? "font-semibold text-accent" : "text-ink-400",
            )}
          >
            {index}. {words.title}
          </span>
          {/* The subtitle is noise once a step is running or done, and it is only useful before it
              starts, so it is shown only while the step is still to come. */}
          {cell.status === "pending" && (
            <span className="text-xs text-ink-400">{words.subtitle}</span>
          )}
        </span>

        {cell.elapsedMs > 0 && (
          <span className="ml-auto shrink-0 text-xs tabular-nums text-ink-400">{elapsed(cell.elapsedMs)}</span>
        )}

        {/* The summary sits inside the button on purpose. A reader's eye goes to the sentence, not to
            the title strip above it, so the sentence has to be the thing they press. */}
        {cell.summary && (
          <span className="w-full basis-full pl-8 text-xs text-ink-500">{cell.summary}</span>
        )}
        {done && !open && (
          <ChevronDown className={cn("size-3.5 shrink-0 text-ink-400", pinned && "rotate-180")} />
        )}
      </button>

      {showBody && <Panel>{children}</Panel>}
    </li>
  );
}

/** The detail for whichever step is open. Remounted on a change of step so it scrolls into view. */
function Panel({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    // Optional call: not every environment implements it, and a panel that failed to scroll is still
    // a panel the reader needs.
    ref.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, []);
  return (
    <div ref={ref} className="min-w-0 overflow-x-auto border-t border-ink-200 px-4 py-4">
      {children}
    </div>
  );
}
