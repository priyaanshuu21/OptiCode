import { useMemo, useState, type JSX } from 'react'

import { analysisOf, stageSkipped } from '@/api/client'
import type { Finding, Severity } from '@/api/types'
import { formatLines, formatSpan } from '@/lib/span'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel, severityColour, severityDot } from '@/components/ui'

/**
 * Dead assignments, unreachable code, and conditions the solver decided.
 *
 * A finding is only worth reading if it is credible, so the panel shows the evidence rather than
 * just the verdict: the write, the reassignment that kills it, the live set in between. "The
 * value assigned to `y` is overwritten before it is read" is a claim; "`y` is written in B1 on
 * line 2 and the live set after this instruction is {x}, and here is where it is reassigned" is
 * an argument.
 *
 * Findings are grouped by kind rather than listed flat, because the kinds have different
 * consequences: dead code is free to delete, a condition the solver proved constant means a
 * branch is never taken, and a missing return is a bug. A flat list would make the third look
 * like the first. The order is the order of consequence, not alphabetical.
 */

const KIND_LABEL: Record<string, string> = {
  dead_assignment: 'Dead assignment',
  unused_call_result: 'Unused call result',
  unused_read_result: 'Unused read result',
  use_before_initialization: 'Read before initialization',
  unreachable_code: 'Unreachable code',
  conditionally_unreachable: 'Conditionally unreachable',
  constant_condition: 'Constant condition',
  constant_division_by_zero: 'Division by zero',
  missing_return: 'Missing return',
}

/** The order kinds are shown in: things that are simply wrong, then things that merely waste work. */
const KIND_ORDER = [
  'constant_division_by_zero',
  'use_before_initialization',
  'missing_return',
  'constant_condition',
  'unreachable_code',
  'conditionally_unreachable',
  'dead_assignment',
  'unused_call_result',
  'unused_read_result',
]

/** What an optimizer would do with it, in the words the backend uses. */
const ACTION_LABEL: Record<string, string> = {
  will_be_removed: 'an optimizer can delete this',
  kept: 'the value is dead, but this statement has to stay',
  informational: 'nothing is dead here — this is context',
}

/** One finding, with its evidence. */
function FindingCard({ finding }: { finding: Finding }): JSX.Element {
  const select = useCompilerStore((state) => state.select)
  const severity: Severity = finding.severity ?? 'warning'

  return (
    <li className="border-b border-edge/60 px-4 py-3">
      <div className="flex items-start gap-3">
        <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${severityDot(severity)}`} />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-2">
            <span className={`font-mono text-2xs font-semibold uppercase ${severityColour(severity).split(' ')[0]}`}>
              {KIND_LABEL[finding.kind] ?? finding.kind}
            </span>
            {finding.function ? (
              <span className="font-mono text-2xs text-slate-500">
                {finding.function}
                {finding.block ? ` · ${finding.block}` : ''}
              </span>
            ) : null}
            {finding.instruction_ids.length > 0 ? (
              <span className="font-mono text-2xs text-slate-600">
                {finding.instruction_ids.join(', ')}
              </span>
            ) : null}
            <span className="ml-auto shrink-0 font-mono text-2xs text-slate-500">
              {formatSpan(finding.span)}
            </span>
          </p>

          <button
            type="button"
            disabled={!finding.span}
            onClick={() =>
              finding.span
                ? select({
                    line: finding.span.start_line,
                    endLine: Math.max(finding.span.end_line, finding.span.start_line),
                    origin: finding.kind,
                  })
                : undefined
            }
            className="mt-1 block w-full text-left text-xs leading-relaxed text-slate-200 disabled:cursor-default"
          >
            {finding.message}
          </button>

          {finding.explanation ? (
            <p className="mt-1 text-2xs leading-relaxed text-slate-400">{finding.explanation}</p>
          ) : null}

          <p className="mt-1 flex flex-wrap items-center gap-x-2 text-2xs text-slate-500">
            {finding.action ? <span>{ACTION_LABEL[finding.action] ?? finding.action}</span> : null}
            {finding.duplicate_of ? (
              <span title="An earlier stage already reported this in the diagnostics list.">
                also reported as {finding.duplicate_of}
              </span>
            ) : null}
          </p>

          {finding.evidence.length > 0 ? (
            <ul className="mt-1.5 space-y-0.5">
              {finding.evidence.map((item, index) => (
                <li key={index} className="text-2xs leading-relaxed text-slate-500">
                  <span className="font-mono text-slate-600">{formatLines(item.span)}</span> {item.note}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </li>
  )
}

export function FindingsView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const [open, setOpen] = useState<string | null>(null)

  const report = useMemo(() => (response ? analysisOf(response) : null), [response])

  const grouped = useMemo(() => {
    if (!report) {
      return []
    }
    const byKind = new Map<string, Finding[]>()
    for (const finding of report.findings) {
      const bucket = byKind.get(finding.kind)
      if (bucket) {
        bucket.push(finding)
      } else {
        byKind.set(finding.kind, [finding])
      }
    }
    return [...byKind.entries()].sort(
      (a, b) => orderOf(a[0]) - orderOf(b[0]),
    )
  }, [report])

  if (!response) {
    return (
      <Panel title="Findings" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Type a program and its findings will appear here." />
      </Panel>
    )
  }

  if (!report) {
    return (
      <Panel title="Findings" subtitle="The analysis stage did not run.">
        <EmptyState
          title="No findings"
          detail="The analysis stage was skipped, usually because the program did not lower. That is not the same as having nothing to report."
          skipped={stageSkipped(response, 'analysis')}
        />
      </Panel>
    )
  }

  return (
    <Panel
      title="Findings"
      subtitle="Dead code and conditions the solver could decide. Each one carries the evidence behind it."
      actions={
        <div className="flex items-center gap-2">
          {Object.entries(report.summary).map(([kind, count]) => (
            <Count key={kind} label={`${count} ${KIND_LABEL[kind] ?? kind}`}>
              {count} {KIND_LABEL[kind] ?? kind}
            </Count>
          ))}
          <Count label={`${report.finding_count} total`}>{report.finding_count} total</Count>
        </div>
      }
    >
      {report.findings.length === 0 ? (
        <EmptyState
          title="No dead code"
          detail={
            report.unreachable_blocks.length > 0
              ? `Every instruction is reachable, but ${report.unreachable_blocks.length} block(s) are listed as unreachable.`
              : 'Every instruction is reachable and every assignment is read before it is overwritten.'
          }
        />
      ) : (
        <ul>
          {grouped.map(([kind, findings]) => (
            <li key={kind}>
              <button
                type="button"
                onClick={() => setOpen(open === kind ? null : kind)}
                aria-expanded={open === kind}
                className="flex w-full items-center gap-2 bg-surface-raised px-4 py-2 text-left hover:bg-surface-overlay"
              >
                <span className="font-mono text-2xs font-semibold uppercase tracking-wider text-slate-300">
                  {KIND_LABEL[kind] ?? kind}
                </span>
                <Count label={`${findings.length} ${KIND_LABEL[kind] ?? kind}`}>{findings.length}</Count>
                <span className="ml-auto font-mono text-2xs text-slate-600">
                  {open === kind ? 'hide' : 'show'}
                </span>
              </button>
              {open === kind ? (
                <ul>
                  {findings.map((finding) => (
                    <FindingCard key={finding.id} finding={finding} />
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

function orderOf(kind: string): number {
  const index = KIND_ORDER.indexOf(kind)
  return index === -1 ? KIND_ORDER.length : index
}
