import { createContext, useContext, type JSX, type ReactNode } from 'react'

/**
 * Small shared presentational pieces.
 *
 * Every stage view needs the same things — a header explaining what it is showing, an empty
 * state that says *why* it is empty, and a count. Those live here so a view cannot invent a
 * different empty state, because "no results" and "this stage did not run" mean very different
 * things to somebody reading a compiler.
 */

/**
 * The way back to the main workspace, handed down to whichever stage screen is open.
 *
 * A context rather than a prop because the Back button belongs in each panel's own header — at
 * the top-left, next to the title — and threading a callback through eight views for a control
 * they do not otherwise care about would be noise at every one of them. Outside a stage screen
 * the value is `null`, so a panel rendered in the workspace simply has no Back button.
 */
export const StageBackContext = createContext<(() => void) | null>(null)

function BackButton({ onClick }: { onClick: () => void }): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex shrink-0 items-center gap-1 rounded border border-edge px-2.5 py-1 font-mono text-xs text-slate-300 transition-colors hover:border-edge-strong hover:bg-surface-overlay hover:text-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stage-ast"
    >
      <span aria-hidden="true">←</span> Back
    </button>
  )
}

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
  const back = useContext(StageBackContext)
  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label={title}>
      <header className="flex items-start justify-between gap-4 border-b border-edge bg-surface-raised px-5 py-3.5">
        <div className="flex min-w-0 items-start gap-3">
          {back ? <BackButton onClick={back} /> : null}
          <div className="min-w-0">
            <h2 className="text-xl font-semibold tracking-tight text-slate-100">{title}</h2>
            {subtitle ? <p className="mt-1 text-sm leading-relaxed text-slate-300">{subtitle}</p> : null}
          </div>
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
      <p className="text-base font-medium text-slate-100">{title}</p>
      <p className="max-w-md text-sm leading-relaxed text-slate-300">{detail}</p>
      {skipped ? (
        <p className="mt-1 rounded bg-surface-overlay px-2.5 py-1 font-mono text-2xs text-slate-300">
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
