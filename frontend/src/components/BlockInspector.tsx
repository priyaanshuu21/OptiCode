import type { JSX } from 'react'

import type { Block, FunctionCfg, FunctionFacts, InstructionFacts } from '@/api/types'
import { instructionSummary } from '@/lib/ir'
import { formatLines } from '@/lib/span'

/**
 * The right-hand pane: what one block contains, and what the analyses know about it.
 *
 * Shared by the CFG and data-flow views for the same reason the graph is: the two are views of
 * one block, and a reader who switches tabs should not have to find the block again. The
 * inspector takes the facts optionally — the CFG view passes none, the data-flow view passes
 * them — so the same component serves both without either one growing a `if (dataflow)` around
 * every row.
 *
 * The per-instruction facts are the interesting part, because that is the answer to "why did
 * the optimizer delete that?" — the live set on either side of the instruction, the definitions
 * that reach it, and the constant the solver proved for it.
 */

function Facts({ facts }: { facts: InstructionFacts | undefined }): JSX.Element {
  if (!facts) {
    return <span className="text-slate-600">no analysis data</span>
  }
  const live = facts.liveness
  const reaching = facts.reaching
  const constants = facts.constants
  return (
    <span className="space-y-0.5">
      {live ? (
        <span className="block font-mono text-2xs text-slate-400">
          <span className="text-slate-600">live in</span> {live.live_in.join(', ') || '∅'}
          {' · '}
          <span className="text-slate-600">out</span> {live.live_out.join(', ') || '∅'}
          {live.dest_live ? null : (
            <span className="ml-1 text-severity-warning" title="nothing reads this value">
              · dead
            </span>
          )}
        </span>
      ) : null}
      {reaching ? (
        <span className="block font-mono text-2xs text-slate-400">
          <span className="text-slate-600">reaching</span> {reaching.reaching.join(', ') || '∅'}
        </span>
      ) : null}
      {constants?.result ? (
        <span className="block font-mono text-2xs text-slate-400">
          <span className="text-slate-600">proves</span> {constants.result.kind}
          {constants.result.value !== undefined && constants.result.value !== null
            ? ` ${String(constants.result.value)}`
            : ''}
          {constants.result.name ? ` ${constants.result.name}` : ''}
        </span>
      ) : null}
    </span>
  )
}

export function BlockInspector({
  graph,
  block,
  facts,
  order,
}: {
  graph: FunctionCfg
  block: Block
  facts: FunctionFacts | undefined
  order: string[]
}): JSX.Element {
  const incoming = graph.edges.filter((edge) => edge.dst === block.id)
  const outgoing = graph.edges.filter((edge) => edge.src === block.id)

  return (
    <aside
      className="w-80 shrink-0 space-y-3 overflow-auto border-l border-edge bg-surface-sunken p-3"
      aria-label={`Block ${block.id}`}
    >
      <div>
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-sm font-semibold text-stage-block">{block.id}</span>
          <span className="text-2xs text-slate-500">
            {block.instructions.length} instruction{block.instructions.length === 1 ? '' : 's'}
          </span>
        </div>
        <p className="text-2xs leading-relaxed text-slate-500">
          Leader: {block.leader_reason || 'first block'} · source {formatLines(block.span)}
        </p>
        {!block.reachable ? (
          <p className="mt-1 font-mono text-2xs text-severity-warning">
            unreachable — no path from the entry reaches this block
          </p>
        ) : null}
      </div>

      <div>
        <p className="text-2xs uppercase tracking-wider text-slate-500">Edges in</p>
        {incoming.length === 0 ? (
          <p className="font-mono text-2xs text-slate-600">none</p>
        ) : (
          <ul className="mt-0.5 space-y-0.5 font-mono text-2xs text-slate-300">
            {incoming.map((edge) => (
              <li key={`in-${edge.src}-${edge.kind}`} className="truncate">
                <span className="text-slate-500">{edge.src}</span> → here
                <span className="ml-1 text-slate-600">({edge.kind})</span>
                {edge.is_back_edge ? <span className="ml-1 text-severity-warning">· loop</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className="text-2xs uppercase tracking-wider text-slate-500">Edges out</p>
        {outgoing.length === 0 ? (
          <p className="font-mono text-2xs text-slate-600">none — this block ends the path</p>
        ) : (
          <ul className="mt-0.5 space-y-0.5 font-mono text-2xs text-slate-300">
            {outgoing.map((edge) => (
              <li key={`out-${edge.dst}-${edge.kind}`} className="truncate">
                here → <span className="text-slate-500">{edge.dst}</span>
                <span className="ml-1 text-slate-600">({edge.kind})</span>
                {edge.is_back_edge ? <span className="ml-1 text-severity-warning">· loop</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      {block.labels.length > 0 ? (
        <div>
          <p className="text-2xs uppercase tracking-wider text-slate-500">Labels</p>
          <p className="font-mono text-2xs text-slate-300">{block.labels.join(', ')}</p>
        </div>
      ) : null}

      <div>
        <p className="text-2xs uppercase tracking-wider text-slate-500">Instructions</p>
        {block.instructions.length === 0 ? (
          <p className="font-mono text-2xs text-slate-600">empty block</p>
        ) : (
          <ol className="mt-1 space-y-1.5">
            {block.instructions.map((instruction) => (
              <li key={instruction.id} className="border-l border-edge pl-2">
                <p className="font-mono text-2xs leading-relaxed text-slate-300">
                  <span className="text-slate-600">{instruction.id}</span>{' '}
                  {instructionSummary(instruction)}
                </p>
                <Facts facts={facts?.instructions[instruction.id]} />
              </li>
            ))}
          </ol>
        )}
      </div>

      <div>
        <p className="text-2xs uppercase tracking-wider text-slate-500">Function order</p>
        <p className="mt-0.5 font-mono text-2xs leading-relaxed text-slate-400">{order.join(' → ')}</p>
      </div>
    </aside>
  )
}
