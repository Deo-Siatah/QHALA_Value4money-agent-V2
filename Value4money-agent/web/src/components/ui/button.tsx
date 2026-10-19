import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

// Near-monochrome with exactly one accent. The accent is the call to action on a page and nothing
// else: if a second thing on a page were the same blue, neither would be the thing to press.
const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-4",
  {
    variants: {
      variant: {
        accent: "bg-accent text-paper shadow-soft hover:bg-accent-strong",
        solid: "bg-ink-900 text-paper hover:bg-ink-700",
        outline: "border border-ink-300 bg-paper text-ink-900 hover:bg-ink-100",
        ghost: "text-ink-700 hover:bg-ink-100",
        danger: "bg-stop text-paper hover:opacity-90",
      },
      size: {
        md: "h-9 px-4 text-sm",
        sm: "h-8 px-3 text-sm",
        lg: "h-12 px-6 text-base",
      },
    },
    defaultVariants: { variant: "solid", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

/** The same classes for anything that should look like a button but is a link. A download is a
 * navigation, so it must be an anchor, and a button inside an anchor is invalid. */
export const buttonVariants = button;

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(button({ variant, size }), className)} {...props} />;
}
