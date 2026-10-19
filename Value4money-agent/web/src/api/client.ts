// Talking to the API.
//
// Every request goes to a relative /api path, which is the Vite dev proxy. Nothing here sets an
// X-API-Token header, and nothing here reads one: the proxy adds it, because EventSource cannot set
// headers at all and the token must never reach the browser.

import type { Decision, RunEvent, RunListItem, RunSnapshot } from "./types";

const BASE = "/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    throw new ApiError(response.status, await detail(response));
  }
  return (await response.json()) as T;
}

/** The server's own wording for a refusal, which is written to be read rather than to be decoded. */
async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body?.detail) && body.detail[0]?.msg) {
      return String(body.detail[0].msg);
    }
  } catch {
    /* fall through to the status text */
  }
  return `${response.status} ${response.statusText}`;
}

export function startRun(files: File[]): Promise<{ run_id: string; status: RunStatusish }> {
  const body = new FormData();
  for (const file of files) {
    body.append("files", file);
  }
  return request("/runs", { method: "POST", body });
}

type RunStatusish = "queued" | "running" | "awaiting_approval" | "filed" | "rejected" | "failed";

export const listRuns = (): Promise<RunListItem[]> => request("/runs");

export const getRun = (runId: string): Promise<RunSnapshot> => request(`/runs/${runId}`);

export function decide(runId: string, decision: Decision, approvedBy: string, note = ""): Promise<unknown> {
  return request(`/runs/${runId}/approval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, approved_by: approvedBy, note }),
  });
}

export const getReportMarkdown = async (runId: string): Promise<string> => {
  const response = await fetch(`${BASE}/runs/${runId}/report`);
  if (!response.ok) {
    throw new ApiError(response.status, await detail(response));
  }
  return response.text();
};

export const reportUrl = (runId: string, format: "pdf" | "tex"): string =>
  `${BASE}/runs/${runId}/report.${format}`;

/**
 * Subscribe to one run's events.
 *
 * EventSource reconnects on its own, which is what we want: a run outlives a page reload and a
 * dropped connection. It also means the listener must tolerate seeing the same events twice after a
 * reconnect, which is why the reducer is written to be idempotent about step timings.
 */
export function subscribe(runId: string, onEvent: (event: RunEvent) => void): () => void {
  const source = new EventSource(`${BASE}/runs/${runId}/events`);
  const forward = (message: MessageEvent) => {
    try {
      onEvent(JSON.parse(message.data) as RunEvent);
    } catch {
      /* a malformed frame must not kill the stream; the next one will do */
    }
  };
  const types: RunEvent["type"][] = [
    "run.started",
    "step.started",
    "step.finished",
    "model.token",
    "model.done",
    "approval.required",
    "run.finished",
    "run.error",
    "heartbeat",
  ];
  const handlers = types.map((type) => {
    const handler = forward;
    source.addEventListener(type, handler as EventListener);
    return [type, handler] as const;
  });
  return () => {
    for (const [type, handler] of handlers) {
      source.removeEventListener(type, handler as EventListener);
    }
    source.close();
  };
}
