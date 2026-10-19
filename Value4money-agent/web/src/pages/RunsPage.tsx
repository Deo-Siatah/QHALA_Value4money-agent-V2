import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listRuns } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { Card, CardBody } from "../components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "../components/ui/input";
import { elapsed } from "../lib/format";

export function RunsPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: listRuns, refetchInterval: 5000 });

  return (
    <div className="mx-auto max-w-4xl px-5 py-10">
      <h1 className="text-2xl font-semibold tracking-tight text-ink-900">All runs</h1>
      <p className="mt-2 text-sm text-ink-700">
        Reviews this process is holding. A run survives a page reload; it does not survive the server
        being restarted.
      </p>

      <Card className="mt-6">
        <CardBody className="px-0 py-0">
          {runs.isPending && <p className="px-5 py-4 text-sm text-ink-500">Loading…</p>}
          {runs.data?.length === 0 && (
            <p className="px-5 py-8 text-center text-sm text-ink-500">
              No reviews yet.{" "}
              <Link to="/" className="font-medium text-ink-900 underline">
                Start one
              </Link>
              .
            </p>
          )}
          {runs.data && runs.data.length > 0 && (
            <Table>
              <THead>
                <TR>
                  <TH>Tender</TH>
                  <TH>Run</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Time</TH>
                </TR>
              </THead>
              <TBody>
                {runs.data.map((run) => (
                  <TR key={run.run_id}>
                    <TD>
                      <Link
                        to={`/runs/${run.run_id}`}
                        className="font-medium text-ink-900 underline-offset-4 hover:underline"
                      >
                        {run.tender_ref ?? "Reading the bids…"}
                      </Link>
                    </TD>
                    <TD className="font-mono text-xs text-ink-400">{run.run_id}</TD>
                    <TD>
                      <StatusBadge status={run.status} />
                    </TD>
                    <TD className="text-right tabular-nums text-ink-500">
                      {/* A run that has ended has a duration, not a stopwatch. Counting up from its
                          start time for ever would claim a filed run was still working. */}
                      {run.finished_at
                        ? elapsed(Date.parse(run.finished_at) - Date.parse(run.started_at))
                        : elapsed(Date.now() - Date.parse(run.started_at))}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
