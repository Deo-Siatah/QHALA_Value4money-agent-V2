import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

// Badges carry state, never judgement. "done" is the one green in the interface and it belongs to a
// step that finished, never to a bid: there is no green here for a bidder that looks fine.
const badge = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      tone: {
        neutral: "border-ink-300 bg-ink-100 text-ink-700",
        strong: "border-ink-300 bg-paper text-ink-900",
        accent: "border-accent/30 bg-accent-soft text-accent",
        done: "border-done/30 bg-done-soft text-done",
        ask: "border-ask/30 bg-ask/10 text-ask",
        stop: "border-stop/30 bg-stop/10 text-stop",
        thin: "border-transparent bg-transparent text-thin",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badge> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badge({ tone }), className)} {...props} />;
}
