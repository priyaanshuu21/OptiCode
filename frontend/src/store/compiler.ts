import { create } from 'zustand'

import { ApiError, compile, health, listExamples, readExample } from '@/api/client'
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
 * than in the editor component so that switching tabs does not restart it.
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

export const STAGES: { id: StageId; label: string; stage: string; blurb: string }[] = [
  {
    id: 'tokens',
    label: 'Tokens',
    stage: 'lexer',
    blurb: 'What the lexer saw, in order, with spans.',
  },
  {
    id: 'ast',
    label: 'AST',
    stage: 'parser',
    blurb: 'The parse tree, with each node traced back to a source range.',
  },
  {
    id: 'tac',
    label: 'TAC',
    stage: 'ir',
    blurb: 'Three-address code, one instruction per source expression.',
  },
  {
    id: 'cfg',
    label: 'CFG',
    stage: 'cfg',
    blurb: 'Basic blocks and the edges between them. Back edges are loops.',
  },
  {
    id: 'dataflow',
    label: 'Data flow',
    stage: 'dataflow',
    blurb: 'Liveness, reaching definitions and constants, with the solver step by step.',
  },
  {
    id: 'findings',
    label: 'Findings',
    stage: 'analysis',
    blurb: 'Dead assignments and unreachable code, each with the evidence behind it.',
  },
  {
    id: 'optimization',
    label: 'Optimizer',
    stage: 'optimizer',
    blurb: 'What the passes did, what they skipped, and whether the result still runs the same.',
  },
  {
    id: 'diagnostics',
    label: 'Diagnostics',
    stage: 'internal',
    blurb: 'Every message from every stage, grouped by severity.',
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

interface CompilerState {
  source: string
  /** Program input, for the examples that read. Sent only when there is some. */
  stdin: string
  mode: 'analyze' | 'optimize'
  response: CompileResponse | null
  status: 'idle' | 'compiling' | 'ready' | 'error'
  error: string | null
  health: HealthResponse | null
  activeStage: StageId
  selection: Selection | null
  selectedBlock: BlockSelection | null
  examples: ExampleSummary[]
  examplesError: string | null
  loadedExample: string | null

  setSource: (source: string) => void
  setStdin: (stdin: string) => void
  setMode: (mode: 'analyze' | 'optimize') => void
  setActiveStage: (stage: StageId) => void
  select: (selection: Selection | null) => void
  selectBlock: (block: BlockSelection | null) => void
  loadExamples: () => Promise<void>
  loadExample: (name: string) => Promise<void>
  run: () => Promise<void>
  checkHealth: () => Promise<void>
}

let pending: ReturnType<typeof setTimeout> | undefined
/** In-flight request generation, so a slow response cannot overwrite a newer one. */
let generation = 0

export const useCompilerStore = create<CompilerState>((set, get) => ({
  source: DEFAULT_SOURCE,
  stdin: '',
  mode: 'analyze',
  response: null,
  status: 'idle',
  error: null,
  health: null,
  activeStage: 'ast',
  selection: null,
  selectedBlock: null,
  examples: [],
  examplesError: null,
  loadedExample: null,

  setSource: (source) => {
    set({ source })
    schedule(set, get)
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
        selection: null,
        selectedBlock: null,
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
      set({ response, status: 'ready' })
    } catch (cause) {
      if (current !== generation) {
        return
      }
      const message =
        cause instanceof ApiError ? cause.message : (cause as Error).message
      set({ status: 'error', error: message, response: null })
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
