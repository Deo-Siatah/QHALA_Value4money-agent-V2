// A recorded run, for building and demonstrating the interface without spending a model call.
//
// The SSE stream is the one part of this frontend that cannot be tested under a test client, and
// this is the answer to that: open the run page with ?replay=1 and it plays this sequence instead.
// It is also how the interface was built in the first place, which is why the sequence is a real one
// taken from a run of the classroom bids rather than something tidied up for a demo.

import type { ApprovalRequest, RunEvent } from "./types";

export const REPLAY_APPROVAL: ApprovalRequest = {
  run_id: "run-replay",
  tender_ref: "MDE/EDU/2026/CLS/022",
  summary: "Seventeen awards of classroom construction were compared.",
  evidence_status: "has_evidence",
  benchmark: { n: 17, confidence: "high", median_kes: 2839845.72, q1_kes: 1898942.0, q3_kes: 4021089.0 },
  bids: [
    { bidder: "Karuru Works Agency Ltd", total: 1228000, pct_vs_median: -56.7, position: "far_below" },
    { bidder: "Taveta Construction & Works Ltd", total: 2478800, pct_vs_median: -12.7, position: "below_range" },
    { bidder: "Mt Kenya Contractors Ltd", total: 5188200, pct_vs_median: 82.8, position: "far_above" },
  ],
  verify_failures: [],
  verify_ok: true,
  review_markdown: `# Price review evidence: MDE/EDU/2026/CLS/022

- Run ID: run-replay

## Summary

Seventeen awards of classroom construction were compared, and one bid total sits 206.7% above the
median of that pool.

## Comparison pool

- Comparable awards (n): 17
- Median award value: KES 2,839,845.72
- Normal range (Q1-Q3): KES 1,898,942.00 to KES 4,021,089.00
- Confidence: high
- Excluded candidates: different_work (10), scale_mismatch (1)

## Findings

1. Fourteen awards of classroom construction have a median of 2,839,845.72 and a normal range of
   1,898,942.00 to 4,021,089.00.
   Sources: 20240705144130-91671, 20250515115938-124146

2. One bid total is 82.8% above the median of seventeen similar awards, above the upper quartile.
   Sources: 20260625071401-160935
`,
};

/** The stream's own heartbeat, so the replay has the same quiet stretches a real run has. */
const beat = (elapsedMs: number): RunEvent => ({ type: "heartbeat", elapsed_ms: elapsedMs });

export const REPLAY: RunEvent[] = [
  { type: "run.started", run_id: "run-replay", steps: [], started_at: "2026-10-18T09:00:00Z" },
  { type: "step.started", step: "intake", index: 0, at: "2026-10-18T09:00:00Z" },
  { type: "model.token", step: "intake", delta: '{"category": "works", ' },
  { type: "model.token", step: "intake", delta: '"description": "construction of classroom blocks"}' },
  { type: "model.done", step: "intake", prompt: "ChatGroq", elapsed_ms: 6100 },
  { type: "step.finished", step: "intake", index: 0, elapsed_ms: 6100, summary: "3 bids read" },
  { type: "step.started", step: "research", index: 1, at: "2026-10-18T09:00:07Z" },
  beat(1500),
  { type: "step.finished", step: "research", index: 1, elapsed_ms: 7800, summary: "30 candidates" },
  { type: "step.started", step: "quality_check", index: 2, at: "2026-10-18T09:00:15Z" },
  beat(9000),
  beat(17000),
  beat(25000),
  { type: "step.finished", step: "quality_check", index: 2, elapsed_ms: 32410, summary: "pool 17 · excluded 13" },
  { type: "step.started", step: "score", index: 3, at: "2026-10-18T09:00:48Z" },
  { type: "step.finished", step: "score", index: 3, elapsed_ms: 5200, summary: "median 2,839,845.72 · medium confidence" },
  { type: "step.started", step: "draft", index: 4, at: "2026-10-18T09:00:53Z" },
  { type: "model.token", step: "draft", delta: "Seventeen awards of classroom construction were compared, " },
  { type: "model.token", step: "draft", delta: "and one bid total sits 206.7% above the median of that pool." },
  { type: "model.done", step: "draft", prompt: "ChatGroq", elapsed_ms: 27400 },
  { type: "step.finished", step: "draft", index: 4, elapsed_ms: 27400, summary: "3 findings · 5 sources" },
  { type: "step.started", step: "verify_sources", index: 5, at: "2026-10-18T09:01:21Z" },
  { type: "step.finished", step: "verify_sources", index: 5, elapsed_ms: 400, summary: "all checks cleared" },
  { type: "approval.required", request: REPLAY_APPROVAL },
];
