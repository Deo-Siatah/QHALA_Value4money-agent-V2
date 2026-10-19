import { Badge } from "./ui/badge";
import type { RunStatus } from "../api/types";
import { STATUS_WORDS } from "../api/types";

/** The status words, never the enum value. A run that failed says "Stopped", because nothing about it
 * is a verdict on the run's subject, only on whether it completed. */
export function StatusBadge({ status }: { status: RunStatus }) {
  const tone =
    status === "filed"
      ? "strong"
      : status === "awaiting_approval"
        ? "ask"
        : status === "failed" || status === "rejected"
          ? "stop"
          : "neutral";
  return <Badge tone={tone}>{STATUS_WORDS[status]}</Badge>;
}
