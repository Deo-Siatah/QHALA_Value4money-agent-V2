import { Link, useLocation } from "react-router-dom";

/**
 * Hasibu, with the one-line description of what it is.
 *
 * persona.md names Hasibu as a public-sector evidence officer, so the same name and the same
 * description are used here rather than a second name for the interface. The tagline is the sentence
 * the project leads with: this produces evidence for a committee, and it does not decide.
 */
export function Header() {
  const { pathname } = useLocation();
  return (
    <header className="no-print border-b border-ink-200 bg-paper">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-5 py-3">
        <Link to="/" className="flex items-baseline gap-2">
          <span className="text-base font-semibold tracking-tight text-ink-900">Hasibu</span>
          <span className="hidden text-xs text-ink-500 sm:inline">Evidence for a tender committee</span>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          <Link
            to="/"
            className={
              pathname === "/" ? "rounded px-2 py-1 font-medium text-ink-900" : "rounded px-2 py-1 text-ink-500 hover:text-ink-900"
            }
          >
            Start
          </Link>
          <Link
            to="/runs"
            className={
              pathname.startsWith("/runs")
                ? "rounded px-2 py-1 font-medium text-ink-900"
                : "rounded px-2 py-1 text-ink-500 hover:text-ink-900"
            }
          >
            All runs
          </Link>
        </nav>
      </div>
    </header>
  );
}
