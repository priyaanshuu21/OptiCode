import { useEffect, type JSX } from 'react'

import { STAGES, useCompilerStore, type StageId } from '@/store/compiler'
import { SourceEditor } from '@/components/SourceEditor'
import { TokensView } from '@/views/TokensView'
import { AstView } from '@/views/AstView'
import { TacView } from '@/views/TacView'
import { CfgView } from '@/views/CfgView'
import { DataFlowView } from '@/views/DataFlowView'
import { FindingsView } from '@/views/FindingsView'
import { OptimizationView } from '@/views/OptimizationView'
import { DiagnosticsView } from '@/views/DiagnosticsView'

/**
 * The application shell: source on the left, one stage view on the right.
 *
 * The stage list is derived from `STAGES` rather than hard-coded twice, so adding a stage is a
 * one-line change and cannot leave the tabs and the renderer disagreeing about what exists.
 * The count on each tab is the number of diagnostics for that stage, which is what makes a tab
 * worth clicking.
 */

const VIEWS: Record<StageId, () => JSX.Element> = {
  tokens: TokensView,
  ast: AstView,
  tac: TacView,
  cfg: CfgView,
  dataflow: DataFlowView,
  findings: FindingsView,
  optimization: OptimizationView,
  diagnostics: DiagnosticsView,
}

function StageTabs(): JSX.Element {
  const active = useCompilerStore((state) => state.activeStage)
  const setActive = useCompilerStore((state) => state.setActiveStage)
  const response = useCompilerStore((state) => state.response)

  return (
    <nav aria-label="Compiler stages" className="flex shrink-0 items-center gap-1 border-b border-edge px-2">
      {STAGES.map((stage) => {
        const isActive = stage.id === active
        const skipped = response?.stages_skipped.includes(stage.stage) ?? false
        const ran = response?.stages_run.includes(stage.stage) ?? false
        const count = (response?.diagnostics ?? []).filter((d) => d.stage === stage.stage).length
        return (
          <button
            key={stage.id}
            type="button"
            onClick={() => setActive(stage.id)}
            aria-current={isActive ? 'page' : undefined}
            title={stage.blurb}
            className={`relative px-3 py-2 text-xs transition-colors ${
              isActive ? 'text-slate-100' : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            <span className="inline-flex items-center gap-1.5">
              {stage.label}
              {count > 0 ? (
                <span className="rounded-full bg-severity-error/20 px-1.5 font-mono text-2xs text-severity-error">
                  {count}
                </span>
              ) : null}
            </span>
            {/* Three states worth distinguishing: this tab is open, its stage ran, or it did not. */}
            {!ran && response ? (
              <span className="absolute inset-x-2 bottom-1 h-px bg-edge-strong" title="stage skipped" />
            ) : null}
            {skipped ? (
              <span className="absolute right-1 top-1.5 h-1 w-1 rounded-full bg-slate-600" aria-label="stage skipped" />
            ) : null}
            {isActive ? (
              <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-stage-ast" />
            ) : null}
          </button>
        )
      })}
    </nav>
  )
}

/**
 * The examples menu.
 *
 * The examples that are *meant* to fail are grouped separately and marked, because "load this
 * program" means something different for a program the compiler rejects. A menu that hid that
 * would let somebody load a broken example, see errors, and conclude the compiler is broken.
 */
function ExamplePicker(): JSX.Element {
  const examples = useCompilerStore((state) => state.examples)
  const examplesError = useCompilerStore((state) => state.examplesError)
  const loaded = useCompilerStore((state) => state.loadedExample)
  const loadExamples = useCompilerStore((state) => state.loadExamples)
  const loadExample = useCompilerStore((state) => state.loadExample)

  useEffect(() => {
    void loadExamples()
  }, [loadExamples])

  if (examplesError) {
    return (
      <span
        className="font-mono text-2xs text-slate-500"
        title={examplesError}
      >
        examples unavailable
      </span>
    )
  }

  if (examples.length === 0) {
    return <span className="font-mono text-2xs text-slate-600">loading examples…</span>
  }

  const valid = examples.filter((example) => example.valid)
  const invalid = examples.filter((example) => !example.valid)

  return (
    <div className="flex items-center gap-2">
      <label className="sr-only" htmlFor="example-picker">
        Load an example program
      </label>
      <select
        id="example-picker"
        value={loaded ?? ''}
        onChange={(event) => {
          if (event.target.value) {
            void loadExample(event.target.value)
          }
        }}
        className="max-w-56 rounded border border-edge bg-surface-overlay px-2 py-1 font-mono text-xs text-slate-200"
      >
        <option value="">Load an example…</option>
        <optgroup label="Compiles cleanly">
          {valid.map((example) => (
            <option key={example.name} value={example.name}>
              {example.title}
            </option>
          ))}
        </optgroup>
        <optgroup label="Rejected on purpose">
          {invalid.map((example) => (
            <option key={example.name} value={example.name}>
              {example.title} ✗
            </option>
          ))}
        </optgroup>
      </select>
    </div>
  )
}

function Toolbar(): JSX.Element {
  const run = useCompilerStore((state) => state.run)
  const status = useCompilerStore((state) => state.status)
  const error = useCompilerStore((state) => state.error)
  const health = useCompilerStore((state) => state.health)

  return (
    <header className="flex shrink-0 items-center gap-4 border-b border-edge bg-surface-raised px-4 py-2">
      <div className="flex items-baseline gap-2">
        <h1 className="font-mono text-sm font-semibold tracking-tight text-slate-100">OptiCode</h1>
        <span className="font-mono text-2xs text-slate-500">compiler stages</span>
      </div>

      <button
        type="button"
        onClick={() => void run()}
        className="rounded border border-edge-strong px-3 py-1 font-mono text-2xs text-slate-200 hover:bg-surface-overlay"
      >
        Analyze
      </button>

      <ExamplePicker />

      <div className="min-w-0 flex-1">
        {error ? (
          <p className="truncate font-mono text-2xs text-severity-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>

      <span className="flex shrink-0 items-center gap-1.5 font-mono text-2xs text-slate-500">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            status === 'error'
              ? 'bg-severity-error'
              : status === 'compiling'
                ? 'animate-pulse bg-sky-400'
                : status === 'ready'
                  ? 'bg-severity-success'
                  : 'bg-slate-600'
          }`}
        />
        {status === 'compiling' ? 'compiling' : status}
      </span>

      {health ? (
        <span className="shrink-0 font-mono text-2xs text-slate-500" title={health.stages.join(' → ')}>
          v{health.version}
        </span>
      ) : null}
    </header>
  )
}

export function App(): JSX.Element {
  const active = useCompilerStore((state) => state.activeStage)
  const source = useCompilerStore((state) => state.source)
  const checkHealth = useCompilerStore((state) => state.checkHealth)
  const run = useCompilerStore((state) => state.run)
  const View = VIEWS[active]

  useEffect(() => {
    void checkHealth()
  }, [checkHealth])

  // Compile once on mount so the app opens with something to look at, without waiting for a
  // keystroke. The debounce in the store does not apply here, and should not.
  useEffect(() => {
    void run()
    // Intentionally mount-only: `run` is stable, and re-running on every source change would
    // defeat the debounce that `setSource` sets up.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-surface-sunken text-slate-200">
      <Toolbar />
      <main className="flex min-h-0 flex-1">
        <section
          className="flex min-h-0 w-[42%] min-w-72 flex-col border-r border-edge"
          aria-label="Source"
        >
          <div className="flex shrink-0 items-center justify-between border-b border-edge px-3 py-2">
            <h2 className="font-mono text-2xs uppercase tracking-wider text-slate-500">Source</h2>
            <span className="font-mono text-2xs text-slate-600">
              {source.split('\n').length} lines
            </span>
          </div>
          <div className="min-h-0 flex-1">
            <SourceEditor />
          </div>
        </section>
        <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Stage view">
          <StageTabs />
          <View />
        </section>
      </main>
    </div>
  )
}
