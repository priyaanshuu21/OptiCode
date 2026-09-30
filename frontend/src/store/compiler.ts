import { create } from 'zustand'

import { ApiError, compile, health } from '@/api/client'
import type { CompileRequest, CompileResponse, HealthResponse } from '@/api/types'

/**
 * The one piece of application state: the program being edited, and what the compiler said.
 *
 * Kept in a single store rather than per-view because the views are not independent — selecting
 * an AST node is the reason the editor highlights a span, and selecting a block is the reason
 * the source shows that range. Scattering that across component state is how a highlight ends
 * up pointing at the wrong line after one of them re-rendered.
 *
 * Compilation is a request/response cycle with a debounce, and the debounce lives here rather
 * than in the editor component so that switching tabs does not restart it.
 */

const COMPILE_DEBOUNCE_MS = 350

export type StageId = 'tokens' | 'ast' | 'tac' | 'blocks' | 'diagnostics'

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
    id: 'blocks',
    label: 'Blocks',
    stage: 'cfg',
    blurb: 'Basic blocks and the edges between them. Back edges are loops.',
  },
  {
    id: 'diagnostics',
    label: 'Diagnostics',
    stage: 'analysis',
    blurb: 'Everything the compiler wants to tell you, grouped by severity.',
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

interface CompilerState {
  source: string
  mode: 'analyze' | 'optimize'
  response: CompileResponse | null
  status: 'idle' | 'compiling' | 'ready' | 'error'
  error: string | null
  health: HealthResponse | null
  activeStage: StageId
  selection: Selection | null

  setSource: (source: string) => void
  setMode: (mode: 'analyze' | 'optimize') => void
  setActiveStage: (stage: StageId) => void
  select: (selection: Selection | null) => void
  run: () => Promise<void>
  checkHealth: () => Promise<void>
}

let pending: ReturnType<typeof setTimeout> | undefined
/** In-flight request generation, so a slow response cannot overwrite a newer one. */
let generation = 0

export const useCompilerStore = create<CompilerState>((set, get) => ({
  source: DEFAULT_SOURCE,
  mode: 'analyze',
  response: null,
  status: 'idle',
  error: null,
  health: null,
  activeStage: 'ast',
  selection: null,

  setSource: (source) => {
    set({ source })
    schedule(set, get)
  },

  setMode: (mode) => {
    set({ mode })
    void get().run()
  },

  setActiveStage: (activeStage) => set({ activeStage }),

  select: (selection) => set({ selection }),

  run: async () => {
    const { source, mode } = get()
    const current = ++generation
    set({ status: 'compiling', error: null })
    try {
      const response = await compile({ source, mode } as CompileRequest)
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
