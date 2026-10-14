import { useCallback, useEffect, useRef, useState, type JSX } from 'react'

import { STAGES, useCompilerStore } from '@/store/compiler'
import { SourceEditor } from '@/components/SourceEditor'

/**
 * The main workspace: the source editor on the left, the stage menu on the right.
 *
 * This screen has one job — get a reader to a stage. It intentionally shows no diagrams, tables
 * or output: a preview of the AST next to the source is one more thing to read before choosing,
 * and the whole point of the menu is that choosing comes first. The results live on their own
 * screens (see `StageScreen`), reached from here and left with Back.
 *
 * The boundary between the two panes is draggable and its position is remembered, because a
 * reader's preferred split is stable across sessions and a fixed 50/50 is wrong for everyone.
 * The editor keeps a minimum width and the menu keeps one too, so neither can be dragged away
 * to nothing; on a screen too narrow for a side-by-side split, the panes stack instead.
 */

/** Editor and menu minimums, in pixels. Below their sum the panes stack. */
const MIN_EDITOR = 320
const MIN_MENU = 280
const DEFAULT_RATIO = 0.58
const STORAGE_KEY = 'opticode.workspace.split'

/** Keep a ratio inside the range that leaves both panes their minimum width. */
export function clampRatio(ratio: number, width: number): number {
  if (!Number.isFinite(ratio) || width <= 0) {
    return DEFAULT_RATIO
  }
  const min = MIN_EDITOR / width
  const max = 1 - MIN_MENU / width
  // The window is too small for both minimums; fall back to an even split rather than a
  // nonsensical clamp where min > max.
  if (min >= max) {
    return 0.5
  }
  return Math.min(max, Math.max(min, ratio))
}

function readStoredRatio(): number {
  try {
    const stored = Number(window.localStorage.getItem(STORAGE_KEY))
    if (Number.isFinite(stored) && stored > 0 && stored < 1) {
      return stored
    }
  } catch {
    // A browser with storage disabled is not a reason to fail to render a split.
  }
  return DEFAULT_RATIO
}

function persistRatio(ratio: number): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(ratio))
  } catch {
    // Best-effort: the split still works for this session.
  }
}

/** Whether the viewport is wide enough for a side-by-side split. */
function useIsWide(): boolean {
  const [wide, setWide] = useState(() =>
    typeof window === 'undefined' ? true : window.matchMedia('(min-width: 900px)').matches,
  )
  useEffect(() => {
    const query = window.matchMedia('(min-width: 900px)')
    const update = (): void => setWide(query.matches)
    query.addEventListener('change', update)
    update()
    return () => query.removeEventListener('change', update)
  }, [])
  return wide
}

function StageMenu(): JSX.Element {
  const setActiveStage = useCompilerStore((state) => state.setActiveStage)
  const response = useCompilerStore((state) => state.response)

  return (
    <nav aria-label="Compiler stages" className="flex min-h-0 flex-col">
      <div className="flex shrink-0 items-baseline justify-between border-b border-edge px-5 py-3">
        <h2 className="text-lg font-semibold text-slate-100">Stages</h2>
        <span className="text-sm text-slate-400">Choose a stage to explore</span>
      </div>
      <ul className="min-h-0 flex-1 space-y-2 overflow-auto p-4">
        {STAGES.map((stage) => {
          const count = (response?.diagnostics ?? []).filter((d) => d.stage === stage.stage).length
          const ran = response?.stages_run.includes(stage.stage) ?? false
          const skipped = response?.stages_skipped.includes(stage.stage) ?? false
          return (
            <li key={stage.id}>
              <button
                type="button"
                onClick={() => setActiveStage(stage.id)}
                className="group flex w-full items-start justify-between gap-3 rounded-lg border border-edge bg-surface-raised px-4 py-3 text-left transition-colors hover:border-edge-strong hover:bg-surface-overlay focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stage-ast"
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <span className="text-base font-semibold text-slate-100">{stage.label}</span>
                    {count > 0 ? (
                      <span className="rounded-full bg-severity-error/20 px-2 py-0.5 font-mono text-2xs text-severity-error">
                        {count}
                      </span>
                    ) : null}
                    {response && !ran ? (
                      <span
                        className="rounded-full bg-surface-overlay px-2 py-0.5 font-mono text-2xs text-slate-400"
                        title={skipped ? 'this stage was skipped' : 'this stage did not run'}
                      >
                        {skipped ? 'skipped' : 'not run'}
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-0.5 block text-sm leading-snug text-slate-300">
                    {stage.description}
                  </span>
                </span>
                <span
                  aria-hidden="true"
                  className="mt-0.5 shrink-0 text-slate-500 transition-colors group-hover:text-slate-200"
                >
                  →
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

export function MainWorkspace(): JSX.Element {
  const source = useCompilerStore((state) => state.source)
  const wide = useIsWide()
  const containerRef = useRef<HTMLDivElement>(null)
  const [ratio, setRatio] = useState(readStoredRatio)
  const [dragging, setDragging] = useState(false)

  // Keep the stored ratio inside the current container's bounds. A ratio saved on a wide screen
  // can leave one pane under its minimum on a narrower one, so it is re-clamped on every resize
  // rather than trusted as written.
  const clamped = useCallback((value: number): number => {
    const width = containerRef.current?.getBoundingClientRect().width ?? 0
    return clampRatio(value, width)
  }, [])

  useEffect(() => {
    if (!dragging) {
      return
    }
    const onMove = (event: PointerEvent): void => {
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect || rect.width === 0) {
        return
      }
      setRatio(clampRatio((event.clientX - rect.left) / rect.width, rect.width))
    }
    const onUp = (): void => {
      setDragging(false)
      setRatio((current) => {
        persistRatio(current)
        return current
      })
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [dragging])

  // Dragging a separator is a pointer gesture; the keyboard gets the same control through arrow
  // keys, so the split is not mouse-only.
  const onKeyDown = (event: React.KeyboardEvent): void => {
    const step = event.shiftKey ? 0.1 : 0.02
    if (event.key === 'ArrowLeft') {
      setRatio((current) => {
        const next = clamped(current - step)
        persistRatio(next)
        return next
      })
      event.preventDefault()
    } else if (event.key === 'ArrowRight') {
      setRatio((current) => {
        const next = clamped(current + step)
        persistRatio(next)
        return next
      })
      event.preventDefault()
    }
  }

  const editorPane = (
    <section
      className="flex min-h-0 min-w-0 flex-1 flex-col border-edge md:border-r"
      aria-label="Source"
      style={wide ? { flexGrow: 0, flexBasis: `${ratio * 100}%` } : undefined}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-edge bg-surface-raised px-4 py-2.5">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">Source</h2>
        <span className="text-sm text-slate-400">{source.split('\n').length} lines</span>
      </div>
      <div className="min-h-0 flex-1">
        <SourceEditor />
      </div>
    </section>
  )

  const menuPane = (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Stage menu">
      <StageMenu />
    </section>
  )

  return (
    <div
      ref={containerRef}
      className="flex min-h-0 flex-1 flex-col md:flex-row"
      data-testid="main-workspace"
    >
      {editorPane}
      {wide ? (
        // A real separator element: focusable, arrow-key operable, and labelled for what it
        // controls, rather than a bare styled `<div>`.
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the source and stage menu panes"
          aria-valuenow={Math.round(ratio * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          tabIndex={0}
          onPointerDown={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onKeyDown={onKeyDown}
          className={`w-1.5 shrink-0 cursor-col-resize bg-edge transition-colors hover:bg-stage-ast focus-visible:bg-stage-ast focus-visible:outline-none ${
            dragging ? 'bg-stage-ast' : ''
          }`}
        />
      ) : null}
      {menuPane}
    </div>
  )
}
