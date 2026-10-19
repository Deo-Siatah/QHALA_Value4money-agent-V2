import { useQuery } from "@tanstack/react-query";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { Copy, FileCode2, FileDown, Printer } from "lucide-react";
import { useState } from "react";
import { getReportMarkdown, reportUrl } from "../api/client";
import type { RunSnapshot } from "../api/types";
import { Button, buttonVariants } from "./ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "./ui/card";
import { Table } from "./ui/input";
import { cn } from "../lib/utils";

/**
 * The evidence file, rendered as the agent wrote it.
 *
 * The markdown is sanitised because it is model output rendered as HTML. The downloads are the
 * server's own renderings of the same review dict, and the print stylesheet makes the browser's Save
 * as PDF a third option that costs nothing.
 */
export function ReportView({ runId, snapshot }: { runId: string; snapshot: RunSnapshot }) {
  const report = useQuery({
    queryKey: ["report", runId, snapshot.status],
    queryFn: () => getReportMarkdown(runId),
    enabled: snapshot.status !== "queued" && snapshot.status !== "running",
    retry: false,
  });
  const [copied, setCopied] = useState(false);

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle>The evidence file</CardTitle>
        <div className="no-print flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => window.print()}>
            <Printer className="size-4" />
            Print
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!report.data}
            onClick={async () => {
              if (report.data) {
                await navigator.clipboard.writeText(report.data);
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1500);
              }
            }}
          >
            <Copy className="size-4" />
            {copied ? "Copied" : "Copy markdown"}
          </Button>
          <a className={cn(buttonVariants({ variant: "outline", size: "sm" }))} href={reportUrl(runId, "pdf")}>
            <FileDown className="size-4" />
            Download PDF
          </a>
          <a className={cn(buttonVariants({ variant: "outline", size: "sm" }))} href={reportUrl(runId, "tex")}>
            <FileCode2 className="size-4" />
            Download LaTeX
          </a>
        </div>
      </CardHeader>
      <CardBody>
        {report.isPending && <p className="text-sm text-ink-500">Waiting for the report…</p>}
        {report.isError && (
          <p className="text-sm text-ink-500">
            There is no report to show yet. It appears once the file has been written.
          </p>
        )}
        {report.data && (
          <article className="max-w-none text-sm leading-relaxed text-ink-700">
            <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
              {report.data}
            </Markdown>
          </article>
        )}
      </CardBody>
    </Card>
  );
}

/** Findings with the records each rests on. No source, no finding, so the ids are shown beside
 * every one of them rather than collected at the end. */
export function Findings({ snapshot }: { snapshot: RunSnapshot }) {
  if (snapshot.findings.length === 0) {
    return (
      <p className="rounded-md bg-ink-100 px-4 py-3 text-sm text-thin">
        No findings were recorded for this review.
      </p>
    );
  }
  return (
    <Table>
      <tbody className="divide-y divide-ink-200">
        {snapshot.findings.map((finding, index) => (
          <tr key={`${index}-${finding.text.slice(0, 12)}`}>
            <td className="w-8 align-top py-2 text-xs tabular-nums text-ink-400">{index + 1}</td>
            <td className="py-2 align-top text-sm text-ink-700">
              {finding.text}
              <span className="mt-1 block text-xs text-ink-400">
                Sources: {finding.sources.join(", ")}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
