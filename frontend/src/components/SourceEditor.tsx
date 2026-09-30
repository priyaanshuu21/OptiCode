import { useCallback, useEffect, useMemo, useRef, type JSX } from 'react'
import Editor, { type OnMount } from '@monaco-editor/react'
import type * as Monaco from 'monaco-editor'

import { useCompilerStore } from '@/store/compiler'
import type { Diagnostic } from '@/api/types'

/** A stable empty array, so a missing response does not change a hook's dependency identity. */
const NO_DIAGNOSTICS: Diagnostic[] = []

/**
 * The source editor, plus the decoration that links it to the rest of the app.
 *
 * Decorations rather than a selection: the selection is the user's, and stealing it every time
 * a stage view reports a span would fight them for the cursor. A decoration highlights without
 * taking focus, so selecting an AST node lights up the code while the caret stays where it was.
 *
 * Compiler diagnostics become Monaco markers, which is what puts the squiggles under the source
 * without a second diagnostics panel having to stay in sync with this one.
 *
 * Both the markers and the highlight are applied imperatively from effects rather than through
 * props, because Monaco owns its model. That has one wrinkle worth naming: the editor mounts
 * asynchronously, so a compile response that arrives first would otherwise never be applied.
 * Hence the refs — `applyMarkers` and `applySelection` are re-run on mount as well as on change.
 */

/** Monaco's own dark theme, nudged to match the app's surfaces. */
function defineTheme(monaco: typeof Monaco): void {
  monaco.editor.defineTheme('opticode', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'keyword', foreground: 'c4b5fd' },
      { token: 'string', foreground: 'fcd34d' },
      { token: 'number', foreground: '6ee7b7' },
      { token: 'comment', foreground: '64748b', fontStyle: 'italic' },
    ],
    colors: {
      'editor.background': '#0a0e1a',
      'editor.lineHighlightBackground': '#111a2e',
      'editorLineNumber.foreground': '#3f4a63',
      'editorLineNumber.activeForeground': '#94a3b8',
      'editor.selectionBackground': '#1e3a5f',
    },
  })
}

/** Compiler severity to Monaco's. Hints are dropped: a hint is not a squiggle. */
function toSeverity(monaco: typeof Monaco, severity: Diagnostic['severity']): Monaco.MarkerSeverity | null {
  switch (severity) {
    case 'error':
      return monaco.MarkerSeverity.Error
    case 'warning':
      return monaco.MarkerSeverity.Warning
    case 'info':
      return monaco.MarkerSeverity.Info
    default:
      return null
  }
}

export function SourceEditor({ height = '100%' }: { height?: string }): JSX.Element {
  const source = useCompilerStore((state) => state.source)
  const setSource = useCompilerStore((state) => state.setSource)
  const selection = useCompilerStore((state) => state.selection)
  const response = useCompilerStore((state) => state.response)
  const diagnostics = useMemo(() => response?.diagnostics ?? NO_DIAGNOSTICS, [response])

  const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null)
  const monacoRef = useRef<typeof Monaco | null>(null)
  const decorationsRef = useRef<Monaco.editor.IEditorDecorationsCollection | null>(null)
  const diagnosticsRef = useRef<Diagnostic[]>(diagnostics)

  const applyMarkers = useCallback(() => {
    const monaco = monacoRef.current
    const model = editorRef.current?.getModel()
    if (!monaco || !model) {
      return
    }
    const markers = diagnosticsRef.current.flatMap((diagnostic) => {
      const severity = toSeverity(monaco, diagnostic.severity)
      if (!diagnostic.span || severity === null) {
        return []
      }
      return [
        {
          severity,
          message: `[${diagnostic.code}] ${diagnostic.message}`,
          startLineNumber: diagnostic.span.start_line,
          startColumn: diagnostic.span.start_col,
          endLineNumber: diagnostic.span.end_line,
          // Both conventions are end-exclusive, so no adjustment.
          endColumn: diagnostic.span.end_col,
          source: diagnostic.stage,
        },
      ]
    })
    monaco.editor.setModelMarkers(model, 'opticode', markers)
  }, [])

  const applySelection = useCallback(() => {
    const decorations = decorationsRef.current
    if (!decorations) {
      return
    }
    const current = useCompilerStore.getState().selection
    if (!current) {
      decorations.clear()
      return
    }
    decorations.set([
      {
        range: {
          startLineNumber: current.line,
          startColumn: 1,
          endLineNumber: current.endLine,
          endColumn: Number.MAX_SAFE_INTEGER,
        },
        options: {
          isWholeLine: true,
          className: 'selection-line',
          linesDecorationsClassName: 'selection-marker',
        },
      },
    ])
    // Reveal rather than select: the point is to see the range, not to take the cursor.
    editorRef.current?.revealLineInCenter(current.line)
  }, [])

  const handleMount: OnMount = (editor, monaco) => {
    editorRef.current = editor
    monacoRef.current = monaco
    defineTheme(monaco)
    monaco.editor.setTheme('opticode')
    decorationsRef.current = editor.createDecorationsCollection([])
    // The editor can mount after the first compile response has already landed.
    applyMarkers()
    applySelection()
  }

  // The editor is controlled by the store, but re-creating the document on every keystroke would
  // reset undo history, so only write back when the store and the editor genuinely differ — which
  // is the codegen preview replacing the buffer, not ordinary typing.
  useEffect(() => {
    const editor = editorRef.current
    const model = editor?.getModel()
    if (model && model.getValue() !== source) {
      editor?.executeEdits('opticode', [
        { range: model.getFullModelRange(), text: source, forceMoveMarkers: true },
      ])
      editor?.pushUndoStop()
    }
  }, [source])

  useEffect(() => {
    diagnosticsRef.current = diagnostics
    applyMarkers()
  }, [diagnostics, applyMarkers])

  useEffect(() => {
    applySelection()
  }, [selection, applySelection])

  return (
    <Editor
      height={height}
      defaultLanguage="c"
      theme="opticode"
      value={source}
      onChange={(value) => setSource(value ?? '')}
      onMount={handleMount}
      options={{
        fontSize: 13,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        renderLineHighlight: 'all',
        tabSize: 4,
        automaticLayout: true,
        padding: { top: 12, bottom: 12 },
        scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
      }}
      loading={<div className="p-4 text-xs text-slate-500">Loading editor…</div>}
    />
  )
}
