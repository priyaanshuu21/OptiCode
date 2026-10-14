import { useState, type JSX } from 'react'

import { dataflowOf, irOf, stageSkipped } from '@/api/client'
import type { FunctionFacts, Instruction } from '@/api/types'
import { CLASS_COLOURS, classify, effectFlags, formatDest, formatOperand } from '@/lib/ir'
import { formatSpan } from '@/lib/span'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'

/**
 * Three-address code, per function.
 *
 * Two modes, because the two questions are different. *Table* answers "what does this program
 * compute", one instruction per row, with its data-flow facts inline — that is the question
 * most people have. *Text* shows the backend's own formatting of the TAC, which is the question
 * a person has when they want to read it as TAC rather than as a spreadsheet.
 *
 * The data-flow columns are not decoration: whether a store is dead, and what values reach an
 * instruction, are the facts the optimizer's passes consume, so showing them here explains why
 * the next stage does what it does.
 */

type Mode = 'table' | 'text'

export function TacView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)
  const [mode, setMode] = useState<Mode>('table')
  const [functionName, setFunctionName] = useState<string | null>(null)

  if (!response) {
    return (
      <Panel title="TAC" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Type a program and its three-address code will appear here." />
      </Panel>
    )
  }

  const program = irOf(response)

  if (!program) {
    return (
      <Panel title="TAC" subtitle="The lowering stage did not run.">
        <EmptyState
          title="No TAC"
          detail="Lowering was skipped, usually because the semantic stage reported errors. See Diagnostics."
          skipped={stageSkipped(response, 'ir')}
        />
      </Panel>
    )
  }

  const facts = dataflowOf(response)
  const selected = functionName ?? program.functions[0]?.name ?? ''
  const current = program.functions.find((fn) => fn.name === selected) ?? program.functions[0]

  return (
    <Panel
      title="TAC"
      subtitle="Three-address code, one instruction per source expression, with the data-flow facts the optimizer uses."
      actions={
        <div className="flex items-center gap-3">
          {program.functions.length > 1 ? (
            <label className="flex items-center gap-1.5 text-2xs text-slate-400">
              <span className="uppercase tracking-wider">Function</span>
              <select
                value={current?.name ?? ''}
                onChange={(event) => setFunctionName(event.target.value)}
                className="rounded border border-edge bg-surface-overlay px-2 py-1 font-mono text-xs text-slate-200"
              >
                {program.functions.map((fn) => (
                  <option key={fn.name} value={fn.name}>
                    {fn.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="flex rounded border border-edge">
            {(['table', 'text'] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setMode(option)}
                className={`px-2 py-1 text-2xs capitalize ${
                  mode === option ? 'bg-surface-overlay text-slate-100' : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {option}
              </button>
            ))}
          </div>
          <Count label={`${program.instruction_count} instructions`}>
            {program.instruction_count} instr
          </Count>
        </div>
      }
    >
      {!current ? (
        <EmptyState title="No functions" detail="The program lowered to zero functions." />
      ) : mode === 'text' ? (
        <pre className="whitespace-pre p-4 font-mono text-xs leading-relaxed text-slate-300">
          {program.text}
        </pre>
      ) : (
        <FunctionTable
          name={current.name}
          returnType={current.return_type}
          instructions={current.instructions}
          facts={facts ? facts[current.name] : undefined}
          onSelect={(instruction) =>
            select({
              line: instruction.span.start_line,
              endLine: instruction.span.end_line,
              origin: `TAC ${instruction.id}`,
            })
          }
        />
      )}
    </Panel>
  )
}

function FunctionTable({
  name,
  returnType,
  instructions,
  facts,
  onSelect,
}: {
  name: string
  returnType: string
  instructions: Instruction[]
  facts: FunctionFacts | undefined
  onSelect: (instruction: Instruction) => void
}): JSX.Element {
  return (
    <table className="w-full border-collapse text-xs">
      <caption className="sr-only">
        TAC instructions for {name}, which returns {returnType}
      </caption>
      <thead className="sticky top-0 bg-surface-raised text-left text-2xs uppercase tracking-wider text-slate-500">
        <tr>
          <th scope="col" className="px-3 py-2 font-medium">Id</th>
          <th scope="col" className="px-3 py-2 font-medium">Destination</th>
          <th scope="col" className="px-3 py-2 font-medium">Opcode</th>
          <th scope="col" className="px-3 py-2 font-medium">Operands</th>
          <th scope="col" className="px-3 py-2 font-medium">Proves</th>
          <th scope="col" className="px-3 py-2 font-medium">Notes</th>
          <th scope="col" className="px-3 py-2 font-medium">Source</th>
        </tr>
      </thead>
      <tbody>
        {instructions.map((instruction) => {
          const fact = facts?.instructions?.[instruction.id]
          const result = fact?.constants?.result
          // `dest_live === false` says the *value* this instruction computes is never read. It
          // does not say the instruction can go: a call, a division or a print keeps its effect
          // even when nobody reads its result. So "unused result" is a fact and "safe to remove"
          // is a separate claim, made only when the instruction has no other effect.
          const writesValue = instruction.dest != null
          const unused = writesValue && fact?.liveness != null && !fact.liveness.dest_live
          const removable = unused && !instruction.has_side_effects && !instruction.may_trap
          return (
            <tr
              key={instruction.id}
              onClick={() => onSelect(instruction)}
              className="cursor-pointer border-t border-edge/60 align-top hover:bg-surface-overlay"
            >
              <td className="px-3 py-1 font-mono text-slate-400">{instruction.id}</td>
              <td className="px-3 py-1 font-mono text-slate-300">{formatDest(instruction)}</td>
              <td className={`px-3 py-1 font-mono ${CLASS_COLOURS[classify(instruction)]}`}>
                {instruction.opcode}
              </td>
              <td className="px-3 py-1 font-mono text-slate-300">
                {instruction.operands.map(formatOperand).join(', ') || '—'}
                {instruction.format ? (
                  <span className="ml-1 text-amber-300">"{instruction.format.replace(/\n/g, '\\n')}"</span>
                ) : null}
              </td>
              <td className="px-3 py-1 font-mono">
                {result && result.kind === 'const' && result.value !== undefined && result.value !== null ? (
                  <span className="text-emerald-300">{String(result.value)}</span>
                ) : (
                  <span className="text-slate-500">—</span>
                )}
              </td>
              <td className="px-3 py-1">
                {unused ? (
                  <span
                    className="rounded bg-severity-warning/15 px-1.5 py-0.5 font-mono text-2xs text-severity-warning"
                    title="nothing reads the value this instruction computes"
                  >
                    unused result
                  </span>
                ) : null}
                {removable ? (
                  <span
                    className="ml-1 rounded bg-severity-success/15 px-1.5 py-0.5 font-mono text-2xs text-severity-success"
                    title="no side effects, so the optimizer can remove it"
                  >
                    safe to remove
                  </span>
                ) : null}
                {effectFlags(instruction).map((flag) => (
                  <span
                    key={flag}
                    className="ml-1 rounded bg-surface-overlay px-1.5 py-0.5 font-mono text-2xs text-slate-400"
                  >
                    {flag}
                  </span>
                ))}
              </td>
              <td className="whitespace-nowrap px-3 py-1 font-mono text-xs text-slate-400" title={formatSpan(instruction.span)}>
                {instruction.span.start_line === instruction.span.end_line
                  ? `L${instruction.span.start_line}`
                  : `L${instruction.span.start_line}-${instruction.span.end_line}`}
                {instruction.ast_node_id ? (
                  <span className="ml-1 text-slate-500">{instruction.ast_node_id}</span>
                ) : null}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
