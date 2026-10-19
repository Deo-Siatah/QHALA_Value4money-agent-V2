import { useEffect, useRef } from "react";
import { STEP_WORDS, type Step } from "../api/types";

/**
 * Hasibu's own words, as they are written.
 *
 * persona.md:1 calls Hasibu "a public-sector evidence officer", so that is the label used here rather
 * than a product name: Hasibu is both the interface and the officer in it, and a committee member
 * reading a finding should be able to tell whose words they are.
 *
 * This sits inside whichever step is open, so it is a plain block rather than something collapsible:
 * nesting a disclosure inside a disclosure makes the page feel like a filing cabinet.
 */
export function ModelStream({ tokens, open, step }: { tokens: string; open: boolean; step: Step | null }) {
  const end = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (open) {
      end.current?.scrollIntoView?.({ block: "end" });
    }
  }, [tokens, open]);

  if (!tokens) {
    return null;
  }

  return (
    <div className="w-full min-w-0 rounded-md border border-ink-200 bg-ink-100/60 px-4 py-3">
      <p className="text-xs font-medium text-ink-700">
        Hasibu, public-sector evidence officer
        {step && <span className="text-ink-400"> · {STEP_WORDS[step].title}</span>}
        {open && <span className="text-ink-400"> writing…</span>}
      </p>
      <div className="mt-2 max-h-56 overflow-y-auto overflow-x-hidden font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-ink-700">
        {tokens}
        <div ref={end} />
      </div>
    </div>
  );
}