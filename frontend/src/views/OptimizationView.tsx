import { useMemo, useState, type JSX } from 'react'

import { codegenOf, optimizationOf, stageSkipped } from '@/api/client'
import type { IrProgram, OptimizationEvent, OptimizationPayload, SkippedGap } from '@/api/types'
import { formatLines } from '@/lib/span'
import { useCompilerStore, type OptimizationPreview } from '@/store/compiler'
import { Count, EmptyState, Panel, severityDot } from '@/components/ui'

/**
 * The optimization screen: compare, then apply.
 *
 * The screen's one job is to answer "what would the optimizer change, and do I want it?". That is
 * a comparison, not a report, so the source the optimizer proposes sits beside the source the
 * editor holds, both read-only and aligned, with the change marked. The report — the passes, what
 * they skipped, whether the result still runs the same — is kept underneath, because it is what
 * makes the proposal checkable, but it is not the first thing a reader has to get through.
 *
 * Two rules keep the application honest:
 *
 * * **Opening the screen changes nothing.** A proposal is generated only when asked for, and it
 *   is generated into `optimizationPreview` rather than replacing the editor or the report.
 * * **Only a proposal that still matches the editor can be applied.** The preview records the
 *   source it was built from; the moment the editor diverges from that, the Apply control is gone
 *   and a fresh proposal is required. This is what stops a slow response from writing stale code
 *   over newer text.
 */

type Section = 'report' | 'events' | 'diff'

/**
 * Which lines differ, by common prefix and suffix.
 *
 * A full LCS would be more precise and much more code, and precision is not what this is for: the
 * reader needs to see *where* the proposal touches the file, and the changed middle between the
 * shared head and tail does exactly that for the rewrites a source generator produces.
 */
export function diffLines(
  before: string[],
  after: string[],
): { left: Set<number>; right: Set<number> } {
  let start = 0
  while (start < before.length && start < after.length && before[start] === after[start]) {
    start += 1
  }
  let endBefore = before.length - 1
  let endAfter = after.length - 1
  while (endBefore >= start && endAfter >= start && before[endBefore] === after[endAfter]) {
    endBefore -= 1
    endAfter -= 1
  }
  const left = new Set<number>()
  const right = new Set<number>()
  for (let index = start; index <= endBefore; index += 1) {
    left.add(index)
  }
  for (let index = start; index <= endAfter; index += 1) {
    right.add(index)
  }
  return { left, right }
}

/** One read-only code panel, with its changed lines marked. */
function CodePanel({
  title,
  code,
  changed,
  tone,
}: {
  title: string
  code: string
  changed: Set<number>
  tone: 'original' | 'optimized'
}): JSX.Element {
  const lines = code.length > 0 ? code.split('\n') : ['']
  return (
    <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-edge bg-surface-sunken">
      <header className="flex shrink-0 items-center justify-between border-b border-edge px-3 py-2">
        <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
        <span className="font-mono text-2xs text-slate-400">{lines.length} lines</span>
      </header>
      <pre className="min-h-0 flex-1 overflow-auto p-3 font-mono text-sm leading-relaxed text-slate-200">
        {lines.map((line, index) => (
          <div
            key={index}
            className={
              changed.has(index)
                ? tone === 'optimized'
                  ? 'bg-severity-success/10 text-severity-success'
                  : 'bg-severity-error/10 text-slate-300'
                : undefined
            }
          >
            <span aria-hidden="true" className="mr-3 inline-block w-7 select-none text-right text-slate-500">
              {index + 1}
            </span>
            {line === '' ? ' ' : line}
          </div>
        ))}
      </pre>
    </section>
  )
}

/**
 * The compare-and-apply control, and the two panels it acts on.
 *
 * Every state the reader can reach is stated in words: loading, failed, stale, nothing to change,
 * ready, applied. None of them is encoded in colour alone, and none of them leaves the Apply
 * control enabled when applying would be wrong.
 */
function ComparePanel({
  preview,
  previewStale,
  status,
  error,
  applied,
  appliedAnalysisError,
  onGenerate,
  onApply,
}: {
  preview: OptimizationPreview | null
  previewStale: boolean
  status: 'idle' | 'loading' | 'error'
  error: string | null
  applied: boolean
  appliedAnalysisError: string | null
  onGenerate: () => void
  onApply: () => void
}): JSX.Element {
  const original = preview?.originalSource ?? ''
  const optimized = preview?.optimizedSource ?? ''
  const { left, right } = useMemo(
    () =>
      preview?.changed
        ? diffLines(original.split('\n'), optimized.split('\n'))
        : { left: new Set<number>(), right: new Set<number>() },
    [preview, original, optimized],
  )

  const canApply =
    status === 'idle' && applied === false && preview !== null && preview.changed && !previewStale

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-slate-100">Compare and apply</h3>
          <p className="text-sm text-slate-300">
            The optimizer proposes a rewrite of the current source. Nothing changes until you
            apply it.
          </p>
        </div>
        <button
          type="button"
          onClick={onGenerate}
          disabled={status === 'loading'}
          className="rounded border border-edge-strong bg-stage-optimizer/20 px-4 py-2 font-mono text-sm font-semibold text-slate-100 transition-colors hover:bg-stage-optimizer/30 disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stage-ast"
        >
          {status === 'loading' ? 'Optimizing…' : 'Optimize this code'}
        </button>
      </div>

      {status === 'error' && error ? (
        <p className="rounded border border-severity-error/50 bg-severity-error/10 px-3 py-2 text-sm text-severity-error" role="alert">
          Could not generate a preview: {error}. The source is unchanged — fix the program and try
          again.
        </p>
      ) : null}

      {previewStale ? (
        <p className="rounded border border-severity-warning/50 bg-severity-warning/10 px-3 py-2 text-sm text-severity-warning" role="status">
          The code changed since this preview was generated, so applying it is disabled. Generate a
          fresh preview.
        </p>
      ) : null}

      {applied ? (
        <div className="rounded border border-severity-success/50 bg-severity-success/10 px-3 py-2" role="status">
          <p className="text-sm font-medium text-severity-success">Optimized code applied.</p>
          <p className="mt-0.5 text-sm text-slate-300">
            The editor now contains the optimized source and it was re-analyzed.
          </p>
        </div>
      ) : null}

      {applied && appliedAnalysisError ? (
        <p className="rounded border border-severity-error/50 bg-severity-error/10 px-3 py-2 text-sm text-severity-error" role="alert">
          The optimized code was applied, but re-analyzing it failed: {appliedAnalysisError}. Use
          Analyze in the toolbar to retry.
        </p>
      ) : null}

      <div className="flex min-h-[16rem] flex-col gap-3 lg:flex-row">
        <CodePanel title="Original Code" code={original} changed={left} tone="original" />
        <CodePanel title="Optimized Code" code={optimized} changed={right} tone="optimized" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-300">
          {preview === null
            ? 'Generate a preview to see the optimizer’s proposal.'
            : previewStale
              ? 'The preview no longer matches the editor.'
              : !preview.changed
                ? 'No optimization changes available — the source is already in the optimizer’s preferred form.'
                : `${left.size} line(s) removed or changed, ${right.size} line(s) rewritten.${preview.reason ? ` ${preview.reason}` : ''}`}
        </p>
        <button
          type="button"
          onClick={onApply}
          disabled={!canApply}
          className="rounded border border-edge-strong bg-surface-overlay px-4 py-2 font-mono text-sm text-slate-100 transition-colors hover:bg-edge disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stage-ast"
        >
          Apply to Editor
        </button>
      </div>
    </section>
  )
}

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
      <table className="w-full border-collapse font-mono text-xs">
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
                    changed ? 'text-severity-success' : 'text-slate-500'
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
      <p className="font-mono text-xs text-slate-400">
        nothing skipped — every pass found something, and every entry point the oracle can call
        was called
      </p>
    )
  }
  return (
    <ul className="space-y-1">
      {gaps.map((gap) => (
        <li key={`${gap.kind}:${gap.target}`} className="flex items-start gap-2">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
          <span className="min-w-0">
            <span className="font-mono text-xs text-slate-300">{gap.target}</span>{' '}
            <span className="font-mono text-xs text-slate-500">({gap.kind})</span>
            <span className="block text-sm leading-relaxed text-slate-300">{gap.reason}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function FullyOptimized(): JSX.Element {
  return (
    <div className="flex items-start gap-3 p-6">
      {/*
       * An icon rather than a coloured border: the badge is the only green thing on this panel,
       * so it reads as a verdict instead of as another heading. It is decorative — the heading
       * beside it carries the claim, and a screen reader should not hear the shape twice.
       */}
      <svg
        aria-hidden="true"
        viewBox="0 0 20 20"
        className="mt-0.5 h-5 w-5 shrink-0 text-severity-success"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="m4 10.5 4 4 8-9" />
      </svg>
      <div className="min-w-0">
        <h3 className="text-base font-medium text-severity-success">Code is Fully Optimized</h3>
        <p className="mt-1 text-sm leading-relaxed text-slate-300">
          The active source code already contains all applied optimizations. No further passes
          needed.
        </p>
      </div>
    </div>
  )
}

function EventList({ events }: { events: OptimizationEvent[] }): JSX.Element {
  const select = useCompilerStore((state) => state.select)
  if (events.length === 0) {
    return (
      <p className="font-mono text-xs text-slate-400">
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
            <span className="w-40 shrink-0 font-mono text-xs text-slate-400">{event.pass}</span>
            <span className="w-40 shrink-0 font-mono text-xs text-slate-300">{event.kind}</span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm leading-relaxed text-slate-100">{event.detail}</span>
              <span className="mt-0.5 block font-mono text-xs">
                <span className="text-slate-400">{event.before}</span>
                <span className="mx-1 text-slate-500">→</span>
                <span className="text-severity-success">{event.after}</span>
              </span>
            </span>
            <span className="shrink-0 font-mono text-xs text-slate-400">
              {event.span ? formatLines(event.span) : '—'}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

/** The size, passes, skips and verification — the evidence under the proposal. */
function ReportSection({
  report,
  section,
  onSection,
}: {
  report: OptimizationPayload
  section: Section
  onSection: (section: Section) => void
}): JSX.Element {
  const verification = report.verification
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-slate-100">What the optimizer did</h3>
        <div className="flex rounded border border-edge" role="group" aria-label="Section">
          {(['report', 'events', 'diff'] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => onSection(item)}
              aria-pressed={section === item}
              className={`px-3 py-1 font-mono text-xs capitalize transition-colors ${
                section === item ? 'bg-surface-overlay text-slate-100' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      {section === 'report' ? (
        <div className="space-y-4">
          <section>
            <h4 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Size</h4>
            <table className="mt-1 w-full border-collapse font-mono text-sm">
              <caption className="sr-only">Instruction counts before and after</caption>
              <thead>
                <tr className="text-slate-400">
                  <th scope="col" className="py-1 text-left font-normal">measure</th>
                  <th scope="col" className="py-1 text-left font-normal">before</th>
                  <th scope="col" className="py-1 text-left font-normal">after</th>
                </tr>
              </thead>
              <tbody>
                {(['instructions', 'temps', 'labels', 'functions'] as const).map((key) => (
                  <tr key={key} className="border-t border-edge/60">
                    <th scope="row" className="py-1 text-left font-normal text-slate-300">{key}</th>
                    <td className="py-1 text-slate-200">{report.stats.before[key]}</td>
                    <td className="py-1 text-severity-success">{report.stats.after[key]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section>
            <h4 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Passes</h4>
            <table className="mt-1 w-full border-collapse font-mono text-sm">
              <caption className="sr-only">What each pass did</caption>
              <thead>
                <tr className="text-slate-400">
                  <th scope="col" className="py-1 text-left font-normal">pass</th>
                  <th scope="col" className="py-1 text-left font-normal">rounds</th>
                  <th scope="col" className="py-1 text-left font-normal">applied</th>
                  <th scope="col" className="py-1 text-left font-normal">removed</th>
                </tr>
              </thead>
              <tbody>
                {report.passes.map((pass) => (
                  <tr key={pass.name} className="border-t border-edge/60" title={pass.description}>
                    <th scope="row" className="py-1 text-left font-normal text-slate-200">
                      {pass.name}
                      {pass.changed ? null : <span className="ml-1 text-slate-500">(found nothing)</span>}
                    </th>
                    <td className="py-1 text-slate-300">{pass.iterations}</td>
                    <td className="py-1 text-slate-300">{pass.applications}</td>
                    <td className="py-1 text-slate-300">{pass.removed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section>
            <h4 className="text-sm font-semibold uppercase tracking-wider text-slate-400">
              Skipped ({report.skipped.length})
            </h4>
            <div className="mt-1">
              <GapList gaps={report.skipped} />
            </div>
          </section>

          <section>
            <h4 className="text-sm font-semibold uppercase tracking-wider text-slate-400">Verification</h4>
            {!verification ? (
              <p className="font-mono text-xs text-slate-400">
                the oracle did not run — nothing changed, so there was nothing to compare
              </p>
            ) : (
              <ul className="space-y-0.5 font-mono text-xs text-slate-300">
                {/*
                 * Three outcomes, not two. `skipped` means the oracle never ran, which is
                 * neither a pass nor a failure: rendering it as "a run disagreed" would invent
                 * a bug that does not exist, and rendering it as a pass would overstate the
                 * coverage.
                 */}
                <li className="flex items-center gap-1.5">
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      verification.verified
                        ? 'bg-severity-success'
                        : verification.skipped
                          ? 'bg-slate-400'
                          : severityDot('error')
                    }`}
                  />
                  {verification.verified
                    ? `${verification.cases_checked} run(s) matched`
                    : verification.skipped
                      ? '0 runs matched — the oracle did not run, so nothing is confirmed either way'
                      : 'a run disagreed'}
                  {verification.first_difference ? (
                    <span className="text-severity-error">
                      {verification.first_difference.entry}: {verification.first_difference.difference}
                    </span>
                  ) : null}
                </li>
                {verification.cases_truncated ? (
                  <li className="text-slate-400">
                    the case list was cut short, so the count above understates what was compared
                  </li>
                ) : null}
                {verification.cases_inconclusive > 0 ? (
                  <li className="text-severity-warning">
                    {verification.cases_inconclusive} run(s) hit a limit and decided nothing
                  </li>
                ) : null}
                {verification.unverified_entries.map((entry) => (
                  <li key={entry} className="text-slate-400">
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
            <p className="border-b border-edge px-4 py-2 font-mono text-xs text-severity-warning">
              more rewrites happened than are shown here; the totals above still describe the whole
              run
            </p>
          ) : null}
          <EventList events={report.events} />
        </div>
      ) : (
        <div>
          {/*
           * `codegen.applied === false` is the backend's own statement that its source generator
           * had nothing to change. That is a stronger and more useful claim than
           * `optimization.changed === false`, which also goes false for programs whose source is
           * already optimal while the TAC still holds temporaries the optimizer would fold — work
           * with no source form to show. So the terminal state keys on the source, and the card
           * replaces the diff rather than sitting above it: showing a before/after beside "no
           * further passes needed" invites the reader to treat a TAC-only artifact as unfinished.
           */}
          <TacDiff report={report} />
        </div>
      )}
    </section>
  )
}

function TacDiff({ report }: { report: OptimizationPayload }): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const codegen = response ? codegenOf(response) : null
  const settled = codegen != null && !codegen.applied
  const before = response?.ir ?? null
  const after: IrProgram = report.ir

  if (settled) {
    return <FullyOptimized />
  }
  return <Diff before={before?.text ?? ''} after={after.text} />
}

export function OptimizationView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const status = useCompilerStore((state) => state.status)
  const error = useCompilerStore((state) => state.error)
  const source = useCompilerStore((state) => state.source)
  const compiledSource = useCompilerStore((state) => state.compiledSource)
  const preview = useCompilerStore((state) => state.optimizationPreview)
  const previewStatus = useCompilerStore((state) => state.previewStatus)
  const previewError = useCompilerStore((state) => state.previewError)
  const generate = useCompilerStore((state) => state.generateOptimizationPreview)
  const applyOptimizedSource = useCompilerStore((state) => state.applyOptimizedSource)
  const [section, setSection] = useState<Section>('report')
  const [applied, setApplied] = useState(false)

  const report = useMemo(() => (response ? optimizationOf(response) : null), [response])
  const codegen = useMemo(() => (response ? codegenOf(response) : null), [response])

  // A proposal the main response already carries, used when the source it describes is still the
  // source on screen. This is what makes the screen useful right after an optimize-mode compile,
  // before anyone has pressed the button.
  const inlinePreview = useMemo<OptimizationPreview | null>(() => {
    if (!codegen || (compiledSource !== null && compiledSource !== source)) {
      return null
    }
    const changed = codegen.applied === true && codegen.source !== source
    return {
      originalSource: source,
      optimizedSource: codegen.applied ? codegen.source : source,
      changed,
      reason: codegen.reason ?? '',
    }
  }, [codegen, compiledSource, source])

  // The preview is stale when it exists but was built from different source than the editor now
  // holds. It is shown as a warning, not as the optimized panel.
  const previewStale = preview !== null && preview.originalSource !== source
  const current = preview !== null && !previewStale ? preview : inlinePreview

  if (!response) {
    return (
      <Panel title="Optimization" subtitle="Waiting for the first compile.">
        <EmptyState
          title="Nothing to show yet"
          detail="Type a program, then optimize it to compare the current code with the rewritten code."
        />
      </Panel>
    )
  }

  return (
    <Panel
      title="Optimization"
      subtitle="Compare the current code with the optimizer’s proposed rewrite, then apply it."
      actions={
        report ? (
          <div className="flex items-center gap-2">
            <Count label={`${report.rounds} rounds`}>{report.rounds} rounds</Count>
            <Count label={`${report.removed_instructions} instructions removed`}>
              −{report.removed_instructions} instr
            </Count>
          </div>
        ) : undefined
      }
    >
      <div className="space-y-6 p-4">
        <ComparePanel
          preview={current}
          previewStale={previewStale}
          status={previewStatus}
          error={previewError}
          applied={applied}
          appliedAnalysisError={applied && status === 'error' ? error : null}
          onGenerate={() => {
            setApplied(false)
            void generate()
          }}
          onApply={() => {
            // The store guarantees the source is what the preview describes; the guard here is
            // the view's own, so an apply can never fire against a mismatch.
            if (current && current.changed && current.originalSource === source) {
              setApplied(true)
              applyOptimizedSource(current.optimizedSource)
            }
          }}
        />

        {report ? (
          <ReportSection report={report} section={section} onSection={setSection} />
        ) : (
          <EmptyState
            title="No report"
            detail="The optimizer only runs in optimize mode. Generate a preview above to rewrite the program, or compile in optimize mode for the full report."
            skipped={stageSkipped(response, 'optimizer')}
          />
        )}
      </div>
    </Panel>
  )
}
