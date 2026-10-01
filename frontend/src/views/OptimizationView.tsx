import { useMemo, useState, type JSX } from 'react'

import { codegenOf, optimizationOf, stageSkipped } from '@/api/client'
import type { OptimizationEvent, IrProgram, SkippedGap } from '@/api/types'
import { formatLines } from '@/lib/span'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel, severityDot } from '@/components/ui'

/**
 * What the optimizer did, and what it did not do.
 *
 * The second half is the point. A report that only lists wins invites the wrong conclusion:
 * "removed 7 instructions" reads as "everything removable went", which is only true if nothing
 * was skipped and nothing was left unverified. So the **skipped** list is rendered next to the
 * passes, with the same weight, and a run where every pass found nothing says so in as many
 * words.
 *
 * The diff is a side-by-side of the backend's own TAC text. Reformatting the before/after in the
 * browser would mean a second renderer that eventually disagrees with the first about where the
 * instruction ids go, and a diff keyed on ids is only meaningful if both sides come from the
 * same place.
 */

type Section = 'report' | 'events' | 'diff'

function Diff({ before, after }: { before: string; after: string }): JSX.Element {
  const left = before.split('\n')
  const right = after.split('\n')
  const width = Math.max(left.length, right.length)
  const rows = Array.from({ length: width }, (_value, index) => ({
    left: left[index] ?? '',
    right: right[index] ?? '',
  }))

  return (
    <div className="overflow-auto">
      <table className="w-full border-collapse font-mono text-2xs">
        <caption className="sr-only">Unoptimized TAC beside optimized TAC</caption>
        <thead>
          <tr className="text-slate-500">
            <th scope="col" className="sticky left-0 bg-surface-raised px-3 py-1 text-left font-normal">
              as written
            </th>
            <th scope="col" className="px-3 py-1 text-left font-normal">
              after the optimizer
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const changed = row.left !== row.right
            return (
              <tr key={index} className={changed ? 'bg-surface-overlay/60' : undefined}>
                <td className="whitespace-pre px-3 py-0.5 text-slate-400">{row.left}</td>
                <td
                  className={`whitespace-pre px-3 py-0.5 ${
                    changed ? 'text-severity-success' : 'text-slate-600'
                  }`}
                >
                  {row.right}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function GapList({ gaps }: { gaps: SkippedGap[] }): JSX.Element {
  if (gaps.length === 0) {
    return (
      <p className="font-mono text-2xs text-slate-500">
        nothing skipped — every pass found something, and every entry point the oracle can call
        was called
      </p>
    )
  }
  return (
    <ul className="space-y-1">
      {gaps.map((gap) => (
        <li key={`${gap.kind}:${gap.target}`} className="flex items-start gap-2">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-500" />
          <span className="min-w-0">
            <span className="font-mono text-2xs text-slate-400">{gap.target}</span>{' '}
            <span className="font-mono text-2xs text-slate-600">({gap.kind})</span>
            <span className="block text-2xs leading-relaxed text-slate-500">{gap.reason}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function EventList({ events }: { events: OptimizationEvent[] }): JSX.Element {
  const select = useCompilerStore((state) => state.select)
  if (events.length === 0) {
    return (
      <p className="font-mono text-2xs text-slate-500">
        no rewrites — the passes all ran and none of them matched anything.
      </p>
    )
  }
  return (
    <ul className="divide-y divide-edge/60">
      {events.map((event, index) => (
        <li key={`${event.pass}-${index}`}>
          <button
            type="button"
            disabled={!event.span}
            onClick={() =>
              event.span
                ? select({
                    line: event.span.start_line,
                    endLine: Math.max(event.span.end_line, event.span.start_line),
                    origin: event.kind,
                  })
                : undefined
            }
            className="flex w-full items-start gap-3 px-4 py-2 text-left hover:bg-surface-overlay disabled:cursor-default"
          >
            <span className="w-36 shrink-0 font-mono text-2xs text-slate-500">{event.pass}</span>
            <span className="w-40 shrink-0 font-mono text-2xs text-slate-400">{event.kind}</span>
            <span className="min-w-0 flex-1">
              <span className="block text-2xs leading-relaxed text-slate-200">{event.detail}</span>
              <span className="mt-0.5 block font-mono text-2xs">
                <span className="text-slate-500">{event.before}</span>
                <span className="mx-1 text-slate-600">→</span>
                <span className="text-severity-success">{event.after}</span>
              </span>
            </span>
            <span className="shrink-0 font-mono text-2xs text-slate-500">
              {event.span ? formatLines(event.span) : '—'}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

export function OptimizationView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const original = response?.ir ?? null
  const mode = useCompilerStore((state) => state.mode)
  const setMode = useCompilerStore((state) => state.setMode)
  const run = useCompilerStore((state) => state.run)
  const status = useCompilerStore((state) => state.status)
  const setSource = useCompilerStore((state) => state.setSource)
  const select = useCompilerStore((state) => state.select)
  const [section, setSection] = useState<Section>('report')

  const report = useMemo(() => (response ? optimizationOf(response) : null), [response])
  const codegen = useMemo(() => (response ? codegenOf(response) : null), [response])

  if (!response) {
    return (
      <Panel title="Optimizer" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Switch to optimize mode and the report will appear here." />
      </Panel>
    )
  }

  if (!report) {
    const busy = status === 'compiling'
    return (
      <Panel title="Optimizer" subtitle="The optimizer did not run.">
        <EmptyState
          title="No report"
          detail="The optimizer only runs in optimize mode. Run the optimizer to rewrite the program, or read what the analyses concluded without rewriting anything."
          skipped={stageSkipped(response, 'optimizer')}
        />
        <div className="flex justify-center pb-6">
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              if (mode !== 'optimize') {
                setMode('optimize')
              } else {
                void run()
              }
            }}
            className="rounded border border-edge-strong px-4 py-1.5 font-mono text-xs text-slate-100 hover:bg-surface-overlay disabled:opacity-40"
          >
            {busy ? 'Optimizing…' : 'Run Optimizer'}
          </button>
        </div>
      </Panel>
    )
  }

  const verification = report.verification
  const before = original
  const after: IrProgram = report.ir

  return (
    <Panel
      title="Optimizer"
      subtitle="What each pass did, what it skipped, and whether the result still runs the same."
      actions={
        <div className="flex items-center gap-2">
          <div className="flex rounded border border-edge" role="group" aria-label="Section">
            {(['report', 'events', 'diff'] as const).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setSection(item)}
                aria-pressed={section === item}
                className={`px-2 py-1 font-mono text-2xs capitalize transition-colors ${
                  section === item
                    ? 'bg-surface-overlay text-slate-100'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {item}
              </button>
            ))}
          </div>
          <Count label={`${report.rounds} rounds`}>{report.rounds} rounds</Count>
          <Count label={`${report.removed_instructions} instructions removed`}>
            −{report.removed_instructions} instr
          </Count>
        </div>
      }
    >
      {section === 'report' ? (
        <div className="space-y-4 p-4">
          <section>
            <h3 className="text-2xs uppercase tracking-wider text-slate-500">Size</h3>
            <table className="mt-1 w-full border-collapse font-mono text-2xs">
              <caption className="sr-only">Instruction counts before and after</caption>
              <thead>
                <tr className="text-slate-500">
                  <th scope="col" className="py-1 text-left font-normal">measure</th>
                  <th scope="col" className="py-1 text-left font-normal">before</th>
                  <th scope="col" className="py-1 text-left font-normal">after</th>
                </tr>
              </thead>
              <tbody>
                {(['instructions', 'temps', 'labels', 'functions'] as const).map((key) => (
                  <tr key={key} className="border-t border-edge/60">
                    <th scope="row" className="py-1 text-left font-normal text-slate-400">{key}</th>
                    <td className="py-1 text-slate-300">{report.stats.before[key]}</td>
                    <td className="py-1 text-severity-success">{report.stats.after[key]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section>
            <h3 className="text-2xs uppercase tracking-wider text-slate-500">Passes</h3>
            <table className="mt-1 w-full border-collapse font-mono text-2xs">
              <caption className="sr-only">What each pass did</caption>
              <thead>
                <tr className="text-slate-500">
                  <th scope="col" className="py-1 text-left font-normal">pass</th>
                  <th scope="col" className="py-1 text-left font-normal">rounds</th>
                  <th scope="col" className="py-1 text-left font-normal">applied</th>
                  <th scope="col" className="py-1 text-left font-normal">removed</th>
                </tr>
              </thead>
              <tbody>
                {report.passes.map((pass) => (
                  <tr key={pass.name} className="border-t border-edge/60" title={pass.description}>
                    <th scope="row" className="py-1 text-left font-normal text-slate-300">
                      {pass.name}
                      {pass.changed ? null : (
                        <span className="ml-1 text-slate-600">(found nothing)</span>
                      )}
                    </th>
                    <td className="py-1 text-slate-400">{pass.iterations}</td>
                    <td className="py-1 text-slate-400">{pass.applications}</td>
                    <td className="py-1 text-slate-400">{pass.removed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section>
            <h3 className="text-2xs uppercase tracking-wider text-slate-500">
              Skipped ({report.skipped.length})
            </h3>
            <div className="mt-1">
              <GapList gaps={report.skipped} />
            </div>
          </section>

          <section>
            <h3 className="text-2xs uppercase tracking-wider text-slate-500">Verification</h3>
            {!verification ? (
              <p className="font-mono text-2xs text-slate-500">
                the oracle did not run — nothing changed, so there was nothing to compare
              </p>
            ) : (
              <ul className="space-y-0.5 font-mono text-2xs text-slate-400">
                {/*
                 * Three outcomes, not two. `skipped` means the oracle never ran, which is
                 * neither a pass nor a failure: rendering it as "a run disagreed" would invent
                 * a bug that does not exist, and rendering it as a pass would overstate the
                 * coverage. So it gets its own line, in the neutral colour.
                 */}
                <li className="flex items-center gap-1.5">
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      verification.verified
                        ? 'bg-severity-success'
                        : verification.skipped
                          ? 'bg-slate-500'
                          : severityDot('error')
                    }`}
                  />
                  {verification.verified
                    ? `${verification.cases_checked} run(s) matched`
                    : verification.skipped
                      ? `0 runs matched — the oracle did not run, so nothing is confirmed either way`
                      : 'a run disagreed'}
                  {verification.first_difference ? (
                    <span className="text-severity-error">
                      {verification.first_difference.entry}: {verification.first_difference.difference}
                    </span>
                  ) : null}
                </li>
                {verification.cases_truncated ? (
                  <li className="text-slate-500">
                    the case list was cut short, so the count above understates what was compared
                  </li>
                ) : null}
                {verification.cases_inconclusive > 0 ? (
                  <li className="text-severity-warning">
                    {verification.cases_inconclusive} run(s) hit a limit and decided nothing
                  </li>
                ) : null}
                {verification.unverified_entries.map((entry) => (
                  <li key={entry} className="text-slate-500">
                    {entry} takes parameters, so the oracle had nothing to call it with
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      ) : section === 'events' ? (
        <div>
          {report.events_truncated ? (
            <p className="border-b border-edge px-4 py-2 font-mono text-2xs text-severity-warning">
              more rewrites happened than are shown here; the totals above still describe the whole
              run
            </p>
          ) : null}
          <EventList events={report.events} />
        </div>
      ) : (
        <div>
          {codegen?.source ? (
            <div className="flex items-center justify-between gap-3 border-b border-edge px-4 py-2">
              <p className="font-mono text-2xs text-slate-500">
                {codegen.applied
                  ? 'codegen rewrote the source — apply it to keep editing from the optimized program'
                  : 'the optimized source is ready to edit from'}
              </p>
              <button
                type="button"
                onClick={() => {
                  if (codegen?.source != null) {
                    setSource(codegen.source)
                    select(null)
                  }
                }}
                className="shrink-0 rounded border border-edge-strong px-3 py-1 font-mono text-2xs text-slate-100 hover:bg-surface-overlay"
              >
                Apply to Editor
              </button>
            </div>
          ) : null}
          <Diff before={before?.text ?? ''} after={after.text} />
        </div>
      )}
    </Panel>
  )
}
