// The shapes the API sends, mirroring api/models.py field for field.
//
// The wire is snake_case because the server's pydantic models use their own field names and no alias
// generator. Nothing here is invented: if the server grows a field, this file grows with it, and
// the names in both places are the same words the terminal prints.

/** One run, one order. agent/cli.py:38 prints the same eight. */
export const STEPS = [
  "intake",
  "research",
  "quality_check",
  "score",
  "draft",
  "verify_sources",
  "approval",
  "file",
] as const;

export type Step = (typeof STEPS)[number];

/** What a step is called on screen. "quality_check" is not a word a committee member reads. */
export const STEP_WORDS: Record<Step, { title: string; subtitle: string }> = {
  intake: { title: "Read the bids", subtitle: "Parsing the PDFs" },
  research: { title: "Find comparable awards", subtitle: "Searching past public awards" },
  quality_check: { title: "Check the candidates", subtitle: "Judging which awards are the same work" },
  score: { title: "Benchmark the bids", subtitle: "Placing each total against the pool" },
  draft: { title: "Write the evidence file", subtitle: "The words, not the numbers" },
  verify_sources: { title: "Check every figure", subtitle: "Every number must exist in the data" },
  approval: { title: "Committee decision", subtitle: "Waiting for an approval" },
  file: { title: "File the report", subtitle: "Writing it to the record" },
};

export type RunStatus = "queued" | "running" | "awaiting_approval" | "filed" | "rejected" | "failed";

/** The status words shown on screen. The enum value is never printed. */
export const STATUS_WORDS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Working",
  awaiting_approval: "Waiting for your decision",
  filed: "Filed",
  rejected: "Declined",
  failed: "Stopped",
};

export type StepStatus = "pending" | "running" | "done";

export interface StepTiming {
  step: Step;
  index: number;
  status: StepStatus;
  elapsed_ms: number;
  summary: string;
}

export interface BidView {
  bidder: string;
  total: number | null;
  pct_vs_median: number | null;
  position: string | null;
  comment: string;
  arithmetic_issues: string[];
}

export interface BenchmarkView {
  n: number;
  confidence: string;
  median_kes: number | null;
  q1_kes: number | null;
  q3_kes: number | null;
}

export interface SourceRecord {
  award_id: string;
  ocid?: string | null;
  data_year?: number | null;
  buyer_name?: string | null;
  title?: string | null;
  value_kes?: number | null;
}

export interface Finding {
  text: string;
  sources: string[];
}

export interface RunSnapshot {
  run_id: string;
  status: RunStatus;
  tender_ref: string | null;
  category: string | null;
  description: string | null;
  current_step: Step | null;
  elapsed_ms: number;
  steps: StepTiming[];
  bids: BidView[];
  benchmark: BenchmarkView | null;
  pool_size: number;
  pool_values: number[];
  excluded: Record<string, number>;
  summary: string;
  bid_comments: Record<string, string>;
  findings: Finding[];
  sources: SourceRecord[];
  verify_ok: boolean | null;
  verify_failures: string[];
  errors: string[];
  parse_issues: string[];
  submission: Record<string, unknown> | null;
}

/** The approval node's own payload, sent verbatim inside approval.required.
 *
 * It already carries the whole evidence file. The first version of the gate dropped that field and
 * asked a person to approve a document they could not read, which is the opposite of the point. */
export interface ApprovalRequest {
  run_id: string;
  tender_ref: string | null;
  summary: string;
  evidence_status: "has_evidence" | "no_evidence";
  benchmark: {
    n: number | null;
    confidence: string | null;
    median_kes: number | null;
    q1_kes: number | null;
    q3_kes: number | null;
  };
  bids: { bidder: string; total: number; pct_vs_median: number | null; position: string | null }[];
  verify_ok: boolean;
  verify_failures: string[];
  review_markdown: string;
}

export type RunEvent =
  | { type: "run.started"; run_id: string; steps: string[]; started_at: string }
  | { type: "step.started"; step: Step; index: number; at: string }
  | {
      type: "step.finished";
      step: Step;
      index: number;
      elapsed_ms: number;
      summary?: string;
      data?: Record<string, unknown> | null;
    }
  | { type: "model.token"; step: Step; delta: string }
  | { type: "model.done"; step: Step; prompt: string; elapsed_ms: number }
  | { type: "approval.required"; request: ApprovalRequest }
  | { type: "run.finished"; status: RunStatus; submission?: Record<string, unknown> | null }
  | { type: "run.error"; step?: Step | null; message: string }
  | { type: "heartbeat"; elapsed_ms: number };

export type Decision = "approved" | "rejected" | "publish_anyway";

export interface RunListItem {
  run_id: string;
  status: RunStatus;
  tender_ref: string | null;
  started_at: string;
  finished_at: string | null;
  awaiting_approval: boolean;
}
