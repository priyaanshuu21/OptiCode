import { useMemo, useState, type JSX } from 'react'

import type { Diagnostic, Severity } from '@/api/types'
import { formatSpan } from '@/lib/span'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel, severityColour, severityDot } from '@/components/ui'

/**
 * Every diagnostic the pipeline produced, grouped by severity.
 *
 * Two deliberate choices. Severity ordering is errors first, because a reader scanning for
 * "what is wrong" should not have to sort it themselves. And the filter defaults to showing
 * everything — a filter that hides warnings by default tends to be a filter nobody notices
 * setting.
 *
 * "No diagnostics" and "the pipeline stopped early" are rendered differently on purpose. A
 * compiler that failed to parse has not concluded anything about the program, and showing a
 * green "all clear" there would be a lie.
 */

const SEVERITY_ORDER: Severity[] = ['error', 'warning', 'info', 'hint']

const SEVERITY_LABEL: Record<string, string> = {
  error: 'errors',
  warning: 'warnings',
  info: 'notices',
  hint: 'hints',
}

type Filter = 'all' | Severity

/** A stable empty array, so a missing response does not change a hook's dependency identity. */
const NO_DIAGNOSTICS: Diagnostic[] = []

export function DiagnosticsView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)
  const [filter, setFilter] = useState<Filter>('all')

  const diagnostics = useMemo(
    () => response?.diagnostics ?? NO_DIAGNOSTICS,
    [response],
  )

  const counts = useMemo(() => {
    const tally: Record<string, number> = {}
    for (const diagnostic of diagnostics) {
      tally[diagnostic.severity] = (tally[diagnostic.severity] ?? 0) + 1
    }
    return tally
  }, [diagnostics])

  const visible = useMemo(() => {
    const filtered = filter === 'all' ? diagnostics : diagnostics.filter((d) => d.severity === filter)
    return [...filtered].sort((a, b) => {
      const bySeverity = SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)
      if (bySeverity !== 0) {
        return bySeverity
      }
      const lineA = a.span?.start_line ?? Number.MAX_SAFE_INTEGER
      const lineB = b.span?.start_line ?? Number.MAX_SAFE_INTEGER
      return lineA - lineB
    })
  }, [diagnostics, filter])

  // A pipeline that skipped stages did not examine the rest of the program, so "no diagnostics"
  // is not a clean bill of health.
  const incomplete = (response?.stages_skipped.length ?? 0) > 0
  const stoppedEarly = counts.error !== undefined && counts.error > 0 && incomplete

  if (!response) {
    return (
      <Panel title="Diagnostics" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Type a program and its diagnostics will appear here." />
      </Panel>
    )
  }

  return (
    <Panel
      title="Diagnostics"
      subtitle="Everything the compiler wants to tell you. Select a diagnostic to highlight where it happened."
      actions={
        <div className="flex items-center gap-2">
          {SEVERITY_ORDER.filter((severity) => counts[severity]).map((severity) => (
            <button
              key={severity}
              type="button"
              onClick={() => setFilter(filter === severity ? 'all' : severity)}
              aria-pressed={filter === severity}
              className={`rounded-full border px-2 py-0.5 font-mono text-2xs transition-colors ${
                filter === severity ? severityColour(severity) : 'border-edge text-slate-500 hover:text-slate-300'
              }`}
            >
              {counts[severity]} {SEVERITY_LABEL[severity]}
            </button>
          ))}
          <Count label={`${diagnostics.length} total`}>{diagnostics.length} total</Count>
        </div>
      }
    >
      {diagnostics.length === 0 ? (
        <EmptyState
          title={stoppedEarly ? 'Analysis stopped early' : 'No diagnostics'}
          detail={
            stoppedEarly
              ? `The pipeline reported errors before reaching every stage, so ${response.stages_skipped.join(', ')} never ran. Fix the errors above to get a complete report.`
              : incomplete
                ? `The pipeline completed with some stages skipped: ${response.stages_skipped.join(', ')}.`
                : 'Every stage ran and none of them had anything to report.'
          }
          skipped={incomplete}
        />
      ) : visible.length === 0 ? (
        <EmptyState title={`No ${SEVERITY_LABEL[filter] ?? filter}`} detail="Clear the filter to see everything the compiler reported." />
      ) : (
        <ul className="divide-y divide-edge/60">
          {visible.map((diagnostic: Diagnostic, index: number) => (
            <li key={`${diagnostic.code}:${index}`}>
              <button
                type="button"
                onClick={() =>
                  diagnostic.span
                    ? select({
                        line: diagnostic.span.start_line,
                        endLine: Math.max(diagnostic.span.end_line, diagnostic.span.start_line),
                        origin: `${diagnostic.code}`,
                      })
                    : undefined
                }
                disabled={!diagnostic.span}
                className="flex w-full items-start gap-3 px-4 py-2 text-left hover:bg-surface-overlay disabled:cursor-default"
              >
                <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${severityDot(diagnostic.severity)}`} />
                <span className="w-40 shrink-0">
                  <span className={`block font-mono text-2xs font-semibold ${severityColour(diagnostic.severity).split(' ')[0]}`}>
                    {diagnostic.severity}
                  </span>
                  <span className="block font-mono text-2xs text-slate-500">{diagnostic.stage}</span>
                </span>
                <span className="w-20 shrink-0 font-mono text-2xs text-slate-400">{diagnostic.code}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-xs leading-relaxed text-slate-200">{diagnostic.message}</span>
                  {diagnostic.related && diagnostic.related.length > 0 ? (
                    <span className="mt-1 block space-y-0.5">
                      {diagnostic.related.map((item, relatedIndex) => (
                        <span key={relatedIndex} className="block text-2xs leading-relaxed text-slate-500">
                          <span className="font-mono text-slate-400">{formatSpan(item.span)}</span>{' '}
                          {item.message}
                        </span>
                      ))}
                    </span>
                  ) : null}
                </span>
                <span className="shrink-0 whitespace-nowrap font-mono text-2xs text-slate-500">
                  {formatSpan(diagnostic.span)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
