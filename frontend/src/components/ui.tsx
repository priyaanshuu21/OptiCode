import type { JSX, ReactNode } from 'react'

/**
 * Small shared presentational pieces.
 *
 * Every stage view needs the same things — a header explaining what it is showing, an empty
 * state that says *why* it is empty, and a count. Those live here so a view cannot invent a
 * different empty state, because "no results" and "this stage did not run" mean very different
 * things to somebody reading a compiler.
 */

export function Panel({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string
  subtitle?: string
  actions?: JSX.Element
  children: ReactNode
}): JSX.Element {
  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label={title}>
      <header className="flex items-start justify-between gap-4 border-b border-edge px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold tracking-wide text-slate-200">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p> : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </header>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </section>
  )
}

/**
 * Shown when a panel has nothing to display.
 *
 * The distinction matters: a skipped stage is not an empty result, and a view that cannot tell
 * them apart will cheerfully report "the compiler found no issues" for a program it never
 * finished compiling. `skipped` is therefore its own prop rather than inferred from a length.
 */
export function EmptyState({
  title,
  detail,
  skipped = false,
}: {
  title: string
  detail: string
  skipped?: boolean
}): JSX.Element {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <p className="text-sm font-medium text-slate-300">{title}</p>
      <p className="max-w-md text-xs leading-relaxed text-slate-500">{detail}</p>
      {skipped ? (
        <p className="mt-1 rounded bg-surface-overlay px-2 py-1 font-mono text-2xs text-slate-400">
          stage skipped
        </p>
      ) : null}
    </div>
  )
}

/** A count badge. Rendered as text so it is readable by a screen reader and in a test. */
export function Count({ children, label }: { children: ReactNode; label: string }): JSX.Element {
  return (
    <span
      className="rounded-full bg-surface-overlay px-2 py-0.5 font-mono text-2xs text-slate-300"
      aria-label={label}
    >
      {children}
    </span>
  )
}

/** Severity as a colour class. One place, so a diagnostic list cannot be half-styled. */
export function severityColour(severity: string): string {
  switch (severity) {
    case 'error':
      return 'text-severity-error border-severity-error/40'
    case 'warning':
      return 'text-severity-warning border-severity-warning/40'
    case 'info':
      return 'text-severity-info border-severity-info/40'
    default:
      return 'text-slate-400 border-edge'
  }
}

export function severityDot(severity: string): string {
  switch (severity) {
    case 'error':
      return 'bg-severity-error'
    case 'warning':
      return 'bg-severity-warning'
    case 'info':
      return 'bg-severity-info'
    default:
      return 'bg-slate-500'
  }
}

/** A stage's run/skipped state, as a dot plus a word. */
export function StageState({ ran, name }: { ran: boolean; name: string }): JSX.Element {
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-mono text-2xs ${ran ? 'text-severity-success' : 'text-slate-500'}`}
      title={ran ? `${name} ran` : `${name} did not run`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${ran ? 'bg-severity-success' : 'bg-slate-600'}`} />
      {name}
    </span>
  )
}
