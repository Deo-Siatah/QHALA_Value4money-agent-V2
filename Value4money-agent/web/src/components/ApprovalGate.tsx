import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { AlertTriangle } from "lucide-react";
import type { ApprovalRequest, Decision, RunSnapshot } from "../api/types";
import { KES, MONEY, PERCENT, positionWord } from "../lib/format";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "./ui/card";
import { Input, Label, TBody, TD, TH, THead, TR, Table } from "./ui/input";

/**
 * The gate. Nothing is filed until a named person answers here, and their name goes onto the
 * permanent record with the report.
 *
 * The three buttons are not three shades of the same choice. When the pool held nothing usable there
 * is no benchmark to approve against, so plain approval is not offered at all: the API refuses it
 * with a 422 and names the override, and a button that cannot succeed is worse than no button.
 */
export function ApprovalGate({
  request,
  state,
  onDecide,
  busy,
  error,
}: {
  request: ApprovalRequest;
  state?: RunSnapshot;
  onDecide: (decision: Decision, name: string) => void;
  busy: boolean;
  error: string | null;
}) {
  const [name, setName] = useState("");
  const thin = request.evidence_status === "no_evidence";
  const bench = request.benchmark;

  return (
    <Card className="print-block">
      <CardHeader>
        <CardTitle>
          {thin ? "This report has no comparable awards" : "This report rests on a benchmark"}
        </CardTitle>
        <p className="mt-1 text-sm text-ink-700">{request.summary}</p>
      </CardHeader>
      <CardBody className="space-y-5">
        {thin ? (
          <p className="flex gap-2 rounded-md bg-ink-100 px-3 py-2 text-sm text-ink-700">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-thin" />
            <span>
              Not enough comparable awards to benchmark these bids. Publishing it is still allowed, but
              it says nothing about value for money.
            </span>
          </p>
        ) : (
          <p className="text-sm text-ink-700">
            <Badge tone="strong">{bench.n} comparable awards</Badge>{" "}
            <Badge tone="neutral">{bench.confidence} confidence</Badge>
            <span className="mt-2 block text-ink-500">
              Median {KES(bench.median_kes)} · normal range {KES(bench.q1_kes)} to {KES(bench.q3_kes)}
            </span>
          </p>
        )}

        {request.verify_failures.length > 0 && (
          <div className="rounded-md border border-ink-200 px-3 py-2">
            <p className="text-xs font-semibold text-ink-900">Checks that did not clear</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-ink-500">
              {request.verify_failures.map((failure) => (
                <li key={failure}>{failure}</li>
              ))}
            </ul>
          </div>
        )}

        <BidsTable request={request} state={state} />

        {request.review_markdown && (
          <div>
            <p className="text-xs font-semibold tracking-wide text-ink-500 uppercase">
              The document you are approving
            </p>
            <div className="mt-2 max-h-96 overflow-auto rounded-md border border-ink-200 bg-ink-100/40 px-4 py-3">
              <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                {request.review_markdown}
              </Markdown>
            </div>
          </div>
        )}

        <div className="max-w-xs">
          <Label htmlFor="approver">Your name</Label>
          <Input
            id="approver"
            className="mt-1"
            placeholder="Grace Wanjiku"
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoComplete="name"
          />
          <p className="mt-1 text-xs text-ink-500">
            Recorded against the report with the time. It must be a person, not a role.
          </p>
        </div>

        {error && (
          <p role="alert" className="rounded-md border border-stop/30 bg-stop/5 px-3 py-2 text-sm text-stop">
            {error}
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          {thin ? (
            <Button onClick={() => onDecide("publish_anyway", name)} disabled={busy}>
              File without a benchmark
            </Button>
          ) : (
            <Button onClick={() => onDecide("approved", name)} disabled={busy}>
              Approve and file
            </Button>
          )}
          <Button variant="outline" onClick={() => onDecide("rejected", name)} disabled={busy}>
            Decline
          </Button>
        </div>
        <p className="text-xs text-ink-400">
          Filing writes the report and your name to the permanent record. Declining writes nothing.
        </p>
      </CardBody>
    </Card>
  );
}

function BidsTable({ request, state }: { request: ApprovalRequest; state?: RunSnapshot }) {
  return (
    <Table>
      <caption className="sr-only">Each bid total, its distance from the median, what the report says about it, and any issue in the document itself</caption>
      <THead>
        <TR>
          <TH>Bidder</TH>
          <TH className="text-right">Total (KES)</TH>
          <TH className="text-right">% vs median</TH>
          <TH>Position</TH>
          <TH>Comment</TH>
        </TR>
      </THead>
      <TBody>
        {request.bids.map((bid) => {
          const position = positionWord(bid.position);
          // The comment is what the report says about this bidder, and where a stated total that does
          // not match the bidder's own arithmetic shows up. It belongs in front of the approver.
          const note = state?.bid_comments?.[bid.bidder];
          const issues = state?.bids?.find((row) => row.bidder === bid.bidder)?.arithmetic_issues ?? [];
          const comment = [...issues, ...(note ? [note] : [])].join("; ") || "-";
          return (
            <TR key={bid.bidder}>
              <TD className="font-medium text-ink-900">{bid.bidder}</TD>
              <TD className="text-right tabular-nums">{MONEY(bid.total)}</TD>
              <TD className="text-right tabular-nums">{PERCENT(bid.pct_vs_median)}</TD>
              <TD>
                <span
                  className={
                    position.tone === "ask"
                      ? "inline-flex items-center gap-1.5 font-semibold text-ask"
                      : position.tone === "ask-strong"
                        ? "inline-flex items-center gap-1.5 rounded border border-ask/40 px-1.5 py-0.5 font-bold text-ask"
                        : position.tone === "thin"
                          ? "inline-flex items-center gap-1.5 italic text-thin"
                          : "inline-flex items-center gap-1.5 text-ink-500"
                  }
                >
                  <span aria-hidden="true">{position.marker}</span>
                  {position.label}
                </span>
              </TD>
              <TD className="max-w-xs text-xs text-ink-500">{comment}</TD>
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}
