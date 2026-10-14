import { useCallback, useEffect, type JSX } from 'react'

import { STAGES, useCompilerStore, type StageId } from '@/store/compiler'
import { StageBackContext } from '@/components/ui'
import { MainWorkspace } from '@/components/MainWorkspace'
import { TokensView } from '@/views/TokensView'
import { AstView } from '@/views/AstView'
import { TacView } from '@/views/TacView'
import { CfgView } from '@/views/CfgView'
import { DataFlowView } from '@/views/DataFlowView'
import { FindingsView } from '@/views/FindingsView'
import { OptimizationView } from '@/views/OptimizationView'
import { DiagnosticsView } from '@/views/DiagnosticsView'

/**
 * The application shell.
 *
 * Two screens live here, and only one is on at a time: the **main workspace** (source plus the
 * stage menu) and a **stage screen** for whichever stage the reader chose. Choosing a stage
 * replaces the workspace in place — no new tab, no dialog — and Back returns to it.
 *
 * The workspace is hidden with `display: none` rather than unmounted. Monaco owns its model, its
 * cursor and its undo history; tearing it down whenever a reader peeks at the tokens would throw
 * all three away and rebuilding it is not free. Keeping it mounted makes Back a pure visibility
 * flip, so the editor comes back exactly as it was left.
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

const STAGE_LABEL: Record<StageId, string> = Object.fromEntries(
  STAGES.map((stage) => [stage.id, stage.label]),
) as Record<StageId, string>

/**
 * A stage's own screen: the panel the stage view renders, wrapped so its header carries a Back
 * button. Everything else — selection, results, scroll — is the view's own business, which is
 * why switching in and out does not disturb it: the view is asked to render, not rebuilt.
 */
function StageScreen({ stage }: { stage: StageId }): JSX.Element {
  const setActiveStage = useCompilerStore((state) => state.setActiveStage)
  const back = useCallback(() => setActiveStage(null), [setActiveStage])
  const View = VIEWS[stage]
  return (
    <StageBackContext.Provider value={back}>
      <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label={`${STAGE_LABEL[stage]} screen`}>
        <View />
      </section>
    </StageBackContext.Provider>
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
      <span className="font-mono text-2xs text-slate-400" title={examplesError}>
        examples unavailable
      </span>
    )
  }

  if (examples.length === 0) {
    return <span className="font-mono text-2xs text-slate-400">loading examples…</span>
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
        className="max-w-56 rounded border border-edge bg-surface-overlay px-2.5 py-1.5 font-mono text-sm text-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stage-ast"
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

/**
 * The one control strip, present on every screen.
 *
 * Analyze and Load Example have to be reachable from a stage screen as well as from the
 * workspace — a reader who is looking at a stale AST is exactly the one who wants to recompile —
 * so the toolbar sits above both screens rather than inside either.
 */
function Toolbar(): JSX.Element {
  const run = useCompilerStore((state) => state.run)
  const status = useCompilerStore((state) => state.status)
  const error = useCompilerStore((state) => state.error)
  const health = useCompilerStore((state) => state.health)
  const active = useCompilerStore((state) => state.activeStage)
  const source = useCompilerStore((state) => state.source)
  const compiledSource = useCompilerStore((state) => state.compiledSource)
  const response = useCompilerStore((state) => state.response)

  // Results are stale when they exist, describe a source, and that source is not what the editor
  // currently holds. While a compile is in flight the results are simply pending, not stale.
  const stale =
    status !== 'compiling' &&
    response !== null &&
    compiledSource !== null &&
    compiledSource !== source

  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-edge bg-surface-raised px-4 py-2.5">
      <div className="flex items-baseline gap-2">
        <h1 className="font-mono text-base font-semibold tracking-tight text-slate-100">OptiCode</h1>
        <span className="font-mono text-2xs text-slate-400">
          {active ? STAGE_LABEL[active] : 'compiler stages'}
        </span>
      </div>

      <button
        type="button"
        onClick={() => void run()}
        className="rounded border border-edge-strong bg-surface-overlay px-3.5 py-1.5 font-mono text-sm text-slate-100 transition-colors hover:bg-edge focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stage-ast"
      >
        Analyze
      </button>

      <ExamplePicker />

      {stale ? (
        <span
          className="flex items-center gap-2 rounded-full border border-severity-warning/50 bg-severity-warning/10 px-3 py-1 text-sm text-severity-warning"
          role="status"
        >
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-severity-warning" />
          Source changed — results are out of date
          <button
            type="button"
            onClick={() => void run()}
            className="rounded border border-severity-warning/50 px-2 py-0.5 font-mono text-2xs text-severity-warning hover:bg-severity-warning/20"
          >
            Analyze
          </button>
        </span>
      ) : null}

      <div className="min-w-0 flex-1">
        {error ? (
          <p className="truncate font-mono text-2xs text-severity-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>

      <span className="flex shrink-0 items-center gap-1.5 font-mono text-2xs text-slate-400">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            status === 'error'
              ? 'bg-severity-error'
              : status === 'compiling'
                ? 'animate-pulse bg-sky-400'
                : status === 'ready'
                  ? 'bg-severity-success'
                  : 'bg-slate-500'
          }`}
        />
        {status === 'compiling' ? 'compiling' : status}
      </span>

      {health ? (
        <span className="shrink-0 font-mono text-2xs text-slate-400" title={health.stages.join(' → ')}>
          v{health.version}
        </span>
      ) : null}
    </header>
  )
}

export function App(): JSX.Element {
  const active = useCompilerStore((state) => state.activeStage)
  const checkHealth = useCompilerStore((state) => state.checkHealth)
  const run = useCompilerStore((state) => state.run)

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
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-surface-base text-slate-100">
      <Toolbar />
      <main className="flex min-h-0 flex-1">
        {/* Always mounted; only its visibility changes. See the note above. */}
        <div className={active ? 'hidden' : 'flex min-h-0 min-w-0 flex-1'}>
          <MainWorkspace />
        </div>
        {active ? <StageScreen stage={active} /> : null}
      </main>
    </div>
  )
}
