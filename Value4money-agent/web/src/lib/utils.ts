import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes so a caller can override a component's default without fighting it. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
