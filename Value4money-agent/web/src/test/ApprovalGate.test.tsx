import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ApprovalGate } from "../components/ApprovalGate";
import type { ApprovalRequest, RunSnapshot } from "../api/types";

/**
 * What the approver is shown, and what must never appear in front of them.
 *
 * The rule this file exists to hold: green means a step finished, and it stops there. A committee
 * reading this page is looking at named companies, and a green tick beside a bid would read as an
 * endorsement nothing in this project has authority to give. So the check is made on the rendered
 * element rather than on the source, because that is where the rule actually has to hold.
 */

function request(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    run_id: "run-1",
    tender_ref: "MDE/EDU/2026/CLS/022",
    summary: "Seventeen awards of classroom construction were compared.",
    evidence_status: "has_evidence",
    benchmark: { n: 17, confidence: "high", median_kes: 2839845.72, q1_kes: 1898942, q3_kes: 4021089 },
    bids: [
      { bidder: "Taveta Construction & Works Ltd", total: 2478800, pct_vs_median: -12.7, position: "below_range" },
      { bidder: "Mt Kenya Contractors Ltd", total: 5188200, pct_vs_median: 82.8, position: "far_above" },
      { bidder: "Karuru Works Agency Ltd", total: 1228000, pct_vs_median: -56.7, position: "far_below" },
      { bidder: "Nairobi Classrooms Ltd", total: 2900000, pct_vs_median: 2.1, position: "within_range" },
    ],
    verify_ok: true,
    verify_failures: [],
    review_markdown: "# Price review evidence: T/1\n",
    ...overrides,
  };
}

const snapshot: RunSnapshot = {
  run_id: "run-1",
  status: "awaiting_approval",
  tender_ref: "MDE/EDU/2026/CLS/022",
  category: "works",
  description: "construction of classroom blocks",
  current_step: "approval",
  elapsed_ms: 41200,
  steps: [],
  bids: [
    { bidder: "Mt Kenya Contractors Ltd", total: 5188200, pct_vs_median: 82.8, position: "far_above",
      comment: "", arithmetic_issues: ["Stated total 5188200.00 does not match computed total 5180000.00"] },
  ],
  benchmark: null,
  pool_size: 17,
  pool_values: [],
  excluded: {},
  summary: "",
  bid_comments: { "Mt Kenya Contractors Ltd": "82.8% above the median, above the upper quartile." },
  findings: [],
  sources: [],
  verify_ok: true,
  verify_failures: [],
  errors: [],
  parse_issues: [],
  submission: null,
};

function gate(overrides: Partial<ApprovalRequest> = {}) {
  const onDecide = vi.fn();
  render(
    <ApprovalGate request={request(overrides)} state={snapshot} busy={false} error={null}
      onDecide={onDecide} />,
  );
  return onDecide;
}

describe("the approval gate", () => {
  it("puts the document being approved in front of the person approving it", () => {
    gate();

    expect(screen.getByText("The document you are approving")).toBeInTheDocument();
    expect(screen.getByText("Price review evidence: T/1")).toBeInTheDocument();
  });

  it("shows what the report says about a bidder, and any problem in the bidder's own arithmetic", () => {
    gate();

    expect(screen.getByText(/above the upper quartile/)).toBeInTheDocument();
    expect(screen.getByText(/does not match computed total/)).toBeInTheDocument();
  });

  it("paints no position green, which is reserved for a step that has finished", () => {
    gate();

    for (const position of ["far above the median", "far below the median", "below the normal range"]) {
      const cell = screen.getByText(position).closest("span");
      const classes = cell?.className ?? "";
      expect(classes).not.toMatch(/text-done|text-green|text-accent/);
    }
  });

  it("says what happened in words as well as colour", () => {
    gate();

    expect(screen.getByText("inside the normal range")).toBeInTheDocument();
    expect(screen.getByText("far below the median")).toBeInTheDocument();
  });

  it("offers plain approval when there is a benchmark to approve against", () => {
    gate();

    expect(screen.getByRole("button", { name: /approve and file/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /file without a benchmark/i })).not.toBeInTheDocument();
  });

  it("offers only the override when there is no benchmark, because plain approval would be refused", () => {
    gate({ evidence_status: "no_evidence", verify_ok: false });

    expect(screen.getByRole("button", { name: /file without a benchmark/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve and file/i })).not.toBeInTheDocument();
  });

  it("never takes the decision away: declining is always offered", () => {
    gate({ evidence_status: "no_evidence" });

    expect(screen.getByRole("button", { name: /decline/i })).toBeInTheDocument();
  });

  it("shows the refusals the checks left unresolved, rather than hiding them", () => {
    gate({ verify_ok: false, verify_failures: ["finding 1 quotes 999.5, which is not a figure in the evidence"] });

    expect(screen.getByText(/999.5/)).toBeInTheDocument();
  });

  it("says the name is recorded with the report, because it is", () => {
    gate();

    expect(screen.getByText(/Recorded against the report with the time/)).toBeInTheDocument();
  });
});