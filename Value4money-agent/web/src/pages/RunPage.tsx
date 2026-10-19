import { useEffect, useReducer, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { decide, getRun, subscribe } from "../api/client";
import { initialView, reduce } from "../api/events";
import { REPLAY, REPLAY_APPROVAL } from "../api/replay";
import type { ApprovalRequest, Decision, RunSnapshot, Step } from "../api/types";
import { STEPS } from "../api/types";
import { ApprovalGate } from "../components/ApprovalGate";
import { ModelStream } from "../components/ModelStream";
import { PoolBoxPlot } from "../components/PoolBoxPlot";
import { Findings, ReportView } from "../components/ReportView";
import { StatusBadge } from "../components/StatusBadge";
import { StepStream } from "../components/StepStream";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "../components/ui/card";
import { elapsed, exclusionWord, KES } from "../lib/format";

/** How often the clock ticks between heartbeats. A slow step is silent, and a page that stops
 * counting during a forty second step looks broken. */
const TICK_MS = 250;

export function RunPage() {
  const { runId = "" } = useParams();
  const replay = new URLSearchParams(window.location.search).has("replay");
  const [view, dispatch] = useReducer(reduce, runId, initialView);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const snapshot = useQuery<RunSnapshot>({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId),
    // A run that is still going is read once at a glance and then kept current by the event stream.
    enabled: Boolean(runId) && !replay,
    refetchInterval: replay ? false : 10_000,
  });

  useEffect(() => {
    if (!runId || replay) {
      return;
    }
    return subscribe(runId, (event) => dispatch({ kind: "event", event }));
  }, [runId, replay]);

  useEffect(() => {
    if (replay) {
      for (const event of REPLAY) {
        dispatch({ kind: "event", event });
      }
    }
  }, [replay]);

  useEffect(() => {
    if (snapshot.data) {
      dispatch({ kind: "snapshot", snapshot: snapshot.data });
    }
  }, [snapshot.data]);

  useEffect(() => {
    const timer = window.setInterval(() => dispatch({ kind: "tick", ms: TICK_MS }), TICK_MS);
    return () => window.clearInterval(timer);
  }, []);

  const decideRun = useMutation({
    mutationFn: ({ decision, name }: { decision: Decision; name: string }) =>
      decide(runId, decision, name),
    onError: (problem: Error) => setDecisionError(problem.message),
    onSuccess: () => setDecisionError(null),
  });

  const state = snapshot.data;
  const status = state?.status ?? view.status;
  const steps = state ? stepsFromSnapshot(state) : view.steps;
  const current = state?.current_step ?? view.currentStep;
  const approval: ApprovalRequest | null = view.approval ?? (replay ? REPLAY_APPROVAL : null);
  // While the machine is working, one step fills the slot. Once it pauses for a person or ends, the
  // whole job unfolds into the vertical, reopenable overview.
  const phase: "processing" | "summary" = status === "queued" || status === "running" ? "processing" : "summary";

  const onDecide = (decision: Decision, name: string) => {
    setDecisionError(null);
    decideRun.mutate({ decision, name });
  };

  return (
    <div className="mx-auto max-w-4xl px-5 py-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink-900">
            {state?.tender_ref ?? "Reading the bids…"}
          </h1>
          <p className="mt-1 font-mono text-xs text-ink-400">{runId || "run-replay"}</p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge status={status} />
          <span className="tabular-nums text-sm text-ink-500">{elapsed(view.elapsedMs)}</span>
        </div>
      </header>

      {state?.description && <p className="mt-3 text-sm text-ink-700">{state.description}</p>}

      {view.errors.length > 0 && (
        <div className="mt-4 rounded-md border border-stop/30 bg-stop/5 px-4 py-3">
          <p className="text-sm font-semibold text-stop">Hasibu stopped.</p>
          <p className="text-xs text-ink-500">Nothing was filed.</p>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-ink-700">
            {view.errors.map((problem, index) => (
              <li key={index}>{problem.message}</li>
            ))}
          </ul>
        </div>
      )}

      {!state && status === "queued" && !view.approval && (
        <Card className="mt-6">
          <CardBody className="flex items-center gap-3 text-sm text-ink-500">
            <Loader2 className="size-4 animate-spin" />
            Waiting for the run to start…
          </CardBody>
        </Card>
      )}

      <div className="mt-6">
        <StepStream steps={steps} currentStep={current} phase={phase} body={(step) => (
          <PanelBody step={step} state={state} view={view} approval={approval}
            busy={decideRun.isPending} decisionError={decisionError} onDecide={onDecide}
            runId={runId} status={status} />
        )} />
      </div>
    </div>
  );
}

/** What sits inside whichever step is open. Each one gets only what it is actually about. */
function PanelBody({
  step,
  state,
  view,
  approval,
  busy,
  decisionError,
  onDecide,
  runId,
  status,
}: {
  step: Step;
  state?: RunSnapshot;
  view: ReturnType<typeof initialView>;
  approval: ApprovalRequest | null;
  busy: boolean;
  decisionError: string | null;
  onDecide: (decision: Decision, name: string) => void;
  runId: string;
  status: RunSnapshot["status"] | "queued" | "running";
}) {
  if (step === "intake") {
    return (
      <div className="space-y-2 text-sm">
        {state?.bids.length ? (
          <ul className="divide-y divide-ink-200 rounded-md border border-ink-200">
            {state.bids.map((bid) => (
              <li key={bid.bidder} className="flex items-center justify-between gap-3 px-3 py-2">
                <span className="font-medium text-ink-900">{bid.bidder}</span>
                <span className="tabular-nums text-ink-700">{KES(bid.total)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-ink-500">Reading the bid PDFs…</p>
        )}
        {(state?.parse_issues.length ?? 0) > 0 && (
          <ul className="list-disc space-y-0.5 pl-4 text-xs text-ask">
            {state?.parse_issues.map((issue) => <li key={issue}>{issue}</li>)}
          </ul>
        )}
      </div>
    );
  }

  if (step === "draft") {
    return <ModelStream tokens={view.tokens} open={view.tokensOpen} step={step} />;
  }

  if (step === "quality_check" && state && Object.keys(state.excluded).length > 0) {
    return (
      <div className="flex flex-wrap gap-2">
        {Object.entries(state.excluded).map(([code, count]) => (
          <Badge key={code} tone="thin">{count} {exclusionWord(code)}</Badge>
        ))}
      </div>
    );
  }

  if (step === "score" && state) {
    return (
      <div className="space-y-4">
        {state.benchmark && (
          <p className="text-sm text-ink-700">
            {state.benchmark.n} comparable awards · {state.benchmark.confidence} confidence · median{" "}
            {KES(state.benchmark.median_kes)} · normal range {KES(state.benchmark.q1_kes)} to{" "}
            {KES(state.benchmark.q3_kes)}
          </p>
        )}
        <PoolBoxPlot poolValues={state.pool_values} benchmark={state.benchmark} bids={state.bids} />
      </div>
    );
  }

  if (step === "verify_sources" && state) {
    return (
      <div className="space-y-2 text-sm">
        <p className={state.verify_ok === false ? "text-ask" : "text-ink-700"}>
          {state.verify_ok === null
            ? "The figure checks have not run yet."
            : state.verify_ok
              ? "Every figure in the report exists in the data it was shown."
              : "Some checks did not clear."}
        </p>
        {state.verify_failures.map((failure) => (
          <p key={failure} className="text-xs text-ink-500">{failure}</p>
        ))}
      </div>
    );
  }

  if (step === "approval") {
    if (status === "awaiting_approval" && approval) {
      return (
        <ApprovalGate request={approval} state={state} busy={busy} error={decisionError}
          onDecide={onDecide} />
      );
    }
    return (
      <p className="text-sm text-ink-500">
        {status === "filed"
          ? "A named person approved this report and it was filed."
          : status === "rejected"
            ? "A named person declined this report. Nothing was filed."
            : "Waiting for a named person to decide."}
      </p>
    );
  }

  if (step === "file") {
    if (status === "queued" || status === "running") {
      return <p className="text-sm text-ink-500">Nothing is filed until a named person approves it.</p>;
    }
    return (
      <div className="space-y-4">
        {state && status === "filed" && <Findings snapshot={state} />}
        <ReportView runId={runId} snapshot={state ?? emptySnapshot(runId, status)} />
      </div>
    );
  }

  return null;
}

function emptySnapshot(runId: string, status: RunSnapshot["status"] | "queued" | "running"): RunSnapshot {
  return {
    run_id: runId,
    status: status as RunSnapshot["status"],
    tender_ref: null, category: null, description: null, current_step: null, elapsed_ms: 0,
    steps: STEPS.map((step, index) => ({ step, index, status: "done", elapsed_ms: 0, summary: "" })),
    bids: [], benchmark: null, pool_size: 0, pool_values: [], excluded: {}, summary: "",
    bid_comments: {}, findings: [], sources: [], verify_ok: true, verify_failures: [], errors: [],
    parse_issues: [], submission: null,
  };
}

/** The snapshot already carries the same step truth the events do, in a tidier shape. */
function stepsFromSnapshot(snapshot: RunSnapshot) {
  return Object.fromEntries(
    snapshot.steps.map((timing) => [
      timing.step,
      { status: timing.status, elapsedMs: timing.elapsed_ms, summary: timing.summary ?? "" },
    ]),
  ) as Record<Step, { status: "pending" | "running" | "done"; elapsedMs: number; summary: string }>;
}

/** Kept for the button import the report tab needs. */
export { Card, CardBody, CardHeader, CardTitle, Button };