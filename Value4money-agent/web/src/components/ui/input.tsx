import type { InputHTMLAttributes, LabelHTMLAttributes, TableHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-sm font-medium text-ink-900", className)} {...props} />;
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-md border border-ink-300 bg-paper px-3 text-sm text-ink-900",
        "placeholder:text-ink-400 disabled:opacity-40",
        className,
      )}
      {...props}
    />
  );
}

/** A plain semantic table. The two tables here are small and fixed, so a grid library would be more
 * dependency than the feature is worth. */
export function Table({ className, ...props }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <table className={cn("w-full border-collapse text-sm", className)} {...props} />
  );
}

export function THead({ className, ...props }: TableHTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("border-b border-ink-300", className)} {...props} />;
}

export function TBody({ className, ...props }: TableHTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("", className)} {...props} />;
}

export function TR({ className, ...props }: TableHTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("border-b border-ink-200 last:border-0", className)} {...props} />;
}

export function TH({ className, ...props }: TableHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn("px-3 py-2 text-left text-xs font-semibold tracking-wide text-ink-500", className)}
      {...props}
    />
  );
}

export function TD({ className, ...props }: TableHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-3 py-2 align-top text-ink-700", className)} {...props} />;
}
