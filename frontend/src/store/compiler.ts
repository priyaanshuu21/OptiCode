import { create } from 'zustand'

import { ApiError, codegenOf, compile, health, listExamples, readExample } from '@/api/client'
import type {
  CompileRequest,
  CompileResponse,
  ExampleSummary,
  HealthResponse,
} from '@/api/types'

/**
 * The one piece of application state: the program being edited, and what the compiler said.
 *
 * Kept in a single store rather than per-view because the views are not independent — selecting
 * an AST node is the reason the editor highlights a span, and selecting a block is the reason
 * the source shows that range. Scattering that across component state is how a highlight ends
 * up pointing at the wrong line after one of them re-rendered.
 *
 * The selected *block* is here for the same reason, and it is shared between the CFG and the
 * data-flow view on purpose: they are two readings of one graph, so clicking a block in one and
 * switching tabs should show the same block's facts, not reset to the entry.
 *
 * Compilation is a request/response cycle with a debounce, and the debounce lives here rather
 * than in the editor component so that switching tabs does not restart it. The one place the
 * debounce is skipped is applying the optimizer's codegen output, which replaces the program
 * outright rather than adjusting it a character at a time — see `applyOptimizedSource`.
 */

const COMPILE_DEBOUNCE_MS = 350

export type StageId =
  | 'tokens'
  | 'ast'
  | 'tac'
  | 'cfg'
  | 'dataflow'
  | 'findings'
  | 'optimization'
  | 'diagnostics'

/**
 * The compiler stages, each the name of a course topic and a dedicated screen.
 *
 * `label` is the short name a card shows; `description` is the one line under it, phrased as what
 * the reader gets rather than as what the stage is called. The screen's own panels carry the
 * precise wording, so these stay deliberately plain.
 */
export const STAGES: {
  id: StageId
  label: string
  stage: string
  description: string
}[] = [
  { id: 'tokens', label: 'Tokens', stage: 'lexer', description: 'See how the code is split into tokens.' },
  { id: 'ast', label: 'AST', stage: 'parser', description: 'Explore the structure of your program.' },
  { id: 'tac', label: 'TAC', stage: 'ir', description: 'See the intermediate instructions.' },
  { id: 'cfg', label: 'CFG', stage: 'cfg', description: 'Follow the possible execution paths.' },
  {
    id: 'dataflow',
    label: 'Data Flow',
    stage: 'dataflow',
    description: 'See which variable values are still needed.',
  },
  {
    id: 'findings',
    label: 'Findings',
    stage: 'analysis',
    description: 'Review issues and optimization opportunities.',
  },
  {
    id: 'optimization',
    label: 'Optimization',
    stage: 'optimizer',
    description: 'Compare and apply optimized code.',
  },
  {
    id: 'diagnostics',
    label: 'Diagnostics',
    stage: 'internal',
    description: 'Review compiler errors and warnings.',
  },
]

export const DEFAULT_SOURCE = `int main() {
    int total = 0;
    for (int i = 0; i < 5; i = i + 1) {
        total = total + i;
    }
    printf("sum=%d\\n", total);
    return 0;
}
`

export interface Selection {
  /** Source lines to highlight, 1-based and inclusive. */
  line: number
  endLine: number
  /** What produced the selection, for the status bar. */
  origin: string
}

/**
 * The block a graph view is focused on.
 *
 * `function` travels with the id because block ids are per function: `B1` in `main` is a
 * different block from `B1` in `add`, and a store that remembered only the id would highlight
 * the wrong one after a function switch.
 */
export interface BlockSelection {
  function: string
  block: string
}

/**
 * A proposed rewrite of the current source, generated on demand from the Optimization screen.
 *
 * Kept apart from `response` on purpose. The report in `response` describes the program that was
 * last compiled; the preview is a *proposal* for the source currently in the editor, and the two
 * can be about different programs at the same time. `originalSource` is what makes that checkable
 * — if it no longer matches the editor, the proposal is stale and must not be applied.
 */
export interface OptimizationPreview {
  originalSource: string
  optimizedSource: string
  /** Whether the optimizer actually changed the source (as opposed to only the TAC). */
  changed: boolean
  /** The optimizer's one-line reason, when it has one. */
  reason: string
}

interface CompilerState {
  source: string
  /** Program input, for the examples that read. Sent only when there is some. */
  stdin: string
  mode: 'analyze' | 'optimize'
  response: CompileResponse | null
  /**
   * The source text that `response` describes.
   *
   * The editor holds a live buffer the user can change at any time, and a compile is a request
   * that takes time to come back. Without this, nothing can tell "these results describe what is
   * on screen" from "these results describe the program as it was a moment ago".
   */
  compiledSource: string | null
  status: 'idle' | 'compiling' | 'ready' | 'error'
  error: string | null
  health: HealthResponse | null
  /** Which dedicated screen is open. `null` is the main workspace. */
  activeStage: StageId | null
  selection: Selection | null
  selectedBlock: BlockSelection | null
  examples: ExampleSummary[]
  examplesError: string | null
  loadedExample: string | null
  /** The on-demand optimization proposal for the current source, if one has been generated. */
  optimizationPreview: OptimizationPreview | null
  previewStatus: 'idle' | 'loading' | 'error'
  previewError: string | null

  setSource: (source: string) => void
  applyOptimizedSource: (source: string) => void
  setStdin: (stdin: string) => void
  setMode: (mode: 'analyze' | 'optimize') => void
  setActiveStage: (stage: StageId | null) => void
  select: (selection: Selection | null) => void
  selectBlock: (block: BlockSelection | null) => void
  loadExamples: () => Promise<void>
  loadExample: (name: string) => Promise<void>
  run: () => Promise<void>
  checkHealth: () => Promise<void>
  generateOptimizationPreview: () => Promise<void>
  clearOptimizationPreview: () => void
}

let pending: ReturnType<typeof setTimeout> | undefined
/** In-flight request generation, so a slow response cannot overwrite a newer one. */
let generation = 0
/**
 * Preview generation, separate from `generation` on purpose.
 *
 * A preview is a second compile that neither replaces nor invalidates the report on screen, so
 * it must not share a counter with the main compile — advancing it would cancel an unrelated,
 * still-wanted compile.
 */
let previewGeneration = 0

export const useCompilerStore = create<CompilerState>((set, get) => ({
  source: DEFAULT_SOURCE,
  stdin: '',
  mode: 'analyze',
  response: null,
  compiledSource: null,
  status: 'idle',
  error: null,
  health: null,
  // Opens on the main workspace, not on a stage. Nothing has been chosen yet, so nothing should
  // pretend to be.
  activeStage: null,
  selection: null,
  selectedBlock: null,
  examples: [],
  examplesError: null,
  loadedExample: null,
  optimizationPreview: null,
  previewStatus: 'idle',
  previewError: null,

  setSource: (source) => {
    set({ source })
    schedule(set, get)
  },

  // Applying codegen output is a compile, not a keystroke. Routed through `setSource` it would
  // wait out the debounce and keep the pre-apply response on screen for the whole 350ms, so the
  // badges, the diff and the Apply button would all keep describing the source the editor no
  // longer holds. The result is genuinely a *different program*, so the stale report is cleared
  // rather than dimmed, and the request starts now instead of after a delay nobody asked for.
  applyOptimizedSource: (source) => {
    // Cancel any debounced compile still pending from typing, or its timer fires after this one
    // and recompiles a source the user has since moved on from.
    cancelPending()
    // Invalidate whatever compile is in flight: its response describes the pre-apply source.
    generation += 1
    // One atomic write. Splitting the source from the invalidations would let a render land
    // between them, showing the new text against the old report.
    set({
      source,
      response: null,
      compiledSource: null,
      selection: null,
      selectedBlock: null,
      // The proposal described the pre-apply source and has now been consumed.
      optimizationPreview: null,
      previewStatus: 'idle',
      previewError: null,
      status: 'compiling',
      error: null,
    })
    void get().run()
  },

  // Deliberately not a recompile trigger. stdin only matters to the interpreter, which runs
  // after the analysis the panels show, so typing into it would recompile for nothing and
  // restart the debounce the user did not touch. The Compile button picks it up.
  setStdin: (stdin) => set({ stdin }),

  setMode: (mode) => {
    set({ mode })
    void get().run()
  },

  setActiveStage: (activeStage) => set({ activeStage }),

  select: (selection) => set({ selection }),

  selectBlock: (selectedBlock) => set({ selectedBlock }),

  loadExamples: async () => {
    // Already loaded: do not re-fetch on every render of the toolbar.
    if (get().examples.length > 0) {
      return
    }
    try {
      const listing = await listExamples()
      set({ examples: listing.examples, examplesError: null })
    } catch (cause) {
      // The menu degrades to "unavailable" rather than blocking the app. A missing examples
      // library is not a reason the editor should stop working.
      set({
        examples: [],
        examplesError: cause instanceof ApiError ? cause.message : String(cause),
      })
    }
  },

  loadExample: async (name) => {
    const current = ++generation
    try {
      const detail = await readExample(name)
      if (current !== generation) {
        return
      }
      // One atomic write: a recompile in between would ship the new source with the old stdin.
      set({
        source: detail.source,
        stdin: detail.stdin ?? '',
        mode: detail.suggested_mode === 'optimize' ? 'optimize' : 'analyze',
        loadedExample: name,
        response: null,
        compiledSource: null,
        selection: null,
        selectedBlock: null,
        optimizationPreview: null,
        previewError: null,
      })
      await get().run()
    } catch (cause) {
      if (current !== generation) {
        return
      }
      set({
        error: cause instanceof ApiError ? cause.message : String(cause),
        status: 'error',
      })
    }
  },

  run: async () => {
    const { source, stdin, mode } = get()
    const current = ++generation
    set({ status: 'compiling', error: null })
    try {
      const request: CompileRequest = stdin
        ? { source, mode, stdin }
        : { source, mode }
      const response = await compile(request)
      if (current !== generation) {
        return
      }
      // `source` is the text this request was built from, so recording it is what lets the UI
      // tell these results apart from results for text the user has since edited.
      set({ response, status: 'ready', compiledSource: source })
    } catch (cause) {
      if (current !== generation) {
        return
      }
      const message =
        cause instanceof ApiError ? cause.message : (cause as Error).message
      set({ status: 'error', error: message, response: null, compiledSource: null })
    }
  },

  checkHealth: async () => {
    try {
      set({ health: await health() })
    } catch {
      // A missing health payload is not worth a visible error: the compile request will report
      // the same thing with a better message.
      set({ health: null })
    }
  },

  // A compile of its own, in optimize mode, whose result is a *proposal* and not the report on
  // screen. It deliberately does not touch `response`: opening the Optimization screen must not
  // rewrite the source, and generating a proposal must not discard the results of the last
  // analyze the rest of the app is still showing.
  generateOptimizationPreview: async () => {
    const { source } = get()
    const current = ++previewGeneration
    set({ previewStatus: 'loading', previewError: null })
    try {
      const response = await compile({ source, mode: 'optimize' })
      if (current !== previewGeneration) {
        return
      }
      // The editor moved on while this request was in flight, so the proposal describes text
      // that is no longer on screen. Discarding it is the only way apply can trust a preview.
      if (get().source !== source) {
        set({ previewStatus: 'idle', optimizationPreview: null })
        return
      }
      const codegen = codegenOf(response)
      const changed = codegen?.applied === true && codegen.source !== source
      set({
        previewStatus: 'idle',
        optimizationPreview: {
          originalSource: source,
          optimizedSource: codegen?.applied ? codegen.source : source,
          changed,
          reason: codegen?.reason ?? '',
        },
      })
    } catch (cause) {
      if (current !== previewGeneration) {
        return
      }
      set({
        previewStatus: 'error',
        previewError: cause instanceof ApiError ? cause.message : String(cause),
        optimizationPreview: null,
      })
    }
  },

  clearOptimizationPreview: () => {
    // Invalidate any in-flight generation too, so its result cannot land after the clear.
    previewGeneration += 1
    set({ optimizationPreview: null, previewStatus: 'idle', previewError: null })
  },
}))

function schedule(
  set: (partial: Partial<CompilerState>) => void,
  get: () => CompilerState,
): void {
  if (pending !== undefined) {
    clearTimeout(pending)
  }
  set({ status: 'compiling' })
  pending = setTimeout(() => {
    pending = undefined
    void get().run()
  }, COMPILE_DEBOUNCE_MS)
}

/** Drop the pending debounce, if any, so its timer cannot fire later. */
function cancelPending(): void {
  if (pending !== undefined) {
    clearTimeout(pending)
    pending = undefined
  }
}
