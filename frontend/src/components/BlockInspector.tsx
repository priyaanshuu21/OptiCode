import type { JSX } from 'react'

import type { Block, FunctionCfg, FunctionFacts, Instruction, InstructionFacts } from '@/api/types'
import { instructionSummary } from '@/lib/ir'
import { formatLines } from '@/lib/span'

/**
 * The right-hand pane: what one block contains, and what the analyses know about it.
 *
 * Shared by the CFG and data-flow views for the same reason the graph is: the two are views of
 * one block, and a reader who switches screens should not have to find the block again. The
 * inspector takes the facts optionally — the CFG view passes none, the data-flow view passes
 * them — so the same component serves both without either one growing a `if (dataflow)` around
 * every row.
 *
 * The per-instruction facts are the interesting part, because that is the answer to "why did
 * the optimizer delete that?": the live set on either side of the instruction, and the constant
 * the solver proved for it. What it deliberately does *not* do is call an instruction "dead"
 * because nothing reads its result — see `Facts`.
 */

/**
 * The per-instruction verdict, stated as a fact first and a claim second.
 *
 * `dest_live === false` says the value this instruction computes is never read. It does not say
 * the instruction can go: a call, a division or a print keeps its effect even when its result is
 * discarded. So "value unused" is reported whenever the value is unused, and "safe to remove" is
 * added only when the instruction has no side effect and cannot trap — the conditions under which
 * removal is actually provable. Naming the first as if it were the second is how an optimizer's
 * output ends up misread as "this line does nothing".
 */
function Facts({
  instruction,
  facts,
}: {
  instruction: Instruction
  facts: InstructionFacts | undefined
}): JSX.Element {
  if (!facts) {
    return <span className="text-slate-500">no analysis data</span>
  }
  const live = facts.liveness
  const constants = facts.constants
  const writesValue = instruction.dest != null
  const unused = live != null && writesValue && !live.dest_live
  const removable = unused && !instruction.has_side_effects && !instruction.may_trap
  return (
    <span className="block space-y-0.5">
      {live ? (
        <span className="block font-mono text-xs text-slate-300">
          <span className="text-slate-400">live in</span> {live.live_in.join(', ') || 'None'}
          {' · '}
          <span className="text-slate-400">out</span> {live.live_out.join(', ') || 'None'}
          {unused ? (
            <span
              className={`ml-1 ${removable ? 'text-severity-success' : 'text-severity-warning'}`}
              title={
                removable
                  ? 'nothing reads this value, and the instruction has no other effect'
                  : 'nothing reads this value, but this instruction must still run'
              }
            >
              · {removable ? 'value unused — safe to remove' : 'value unused — must stay'}
            </span>
          ) : null}
        </span>
      ) : null}
      {constants?.result ? (
        <span className="block font-mono text-xs text-slate-300">
          <span className="text-slate-400">proves</span> {constants.result.kind}
          {constants.result.value !== undefined && constants.result.value !== null
            ? ` ${String(constants.result.value)}`
            : ''}
          {constants.result.name ? ` ${constants.result.name}` : ''}
        </span>
      ) : null}
    </span>
  )
}

/** A one-line gloss per set, so a reader does not have to remember the acronyms. */
const LABEL_HELP: Record<string, string> = {
  USE: 'variables read here before this block writes them',
  DEF: 'variables this block writes',
  IN: 'variables live on entry to this block',
  OUT: 'variables live on exit from this block',
}

/**
 * One liveness set, rendered the way the block table renders it.
 *
 * An empty set and a set that was never computed are different facts: the first says the block
 * reads and needs nothing, the second says the analysis did not run here. Collapsing them into
 * one blank cell is the mistake that makes an unreachable block look analysed.
 */
function LiveSet({ label, names, reachable }: { label: string; names?: string[]; reachable: boolean }): JSX.Element {
  return (
    <div className="flex gap-2 font-mono text-xs">
      <span className="w-10 shrink-0 text-slate-400" title={LABEL_HELP[label]}>
        {label}
      </span>
      {names === undefined ? (
        <span className="text-severity-warning">
          {reachable ? 'Not computed' : 'Not analyzed: unreachable'}
        </span>
      ) : (
        <span className="break-words text-slate-100">{names.length > 0 ? names.join(', ') : 'None'}</span>
      )}
    </div>
  )
}

function Heading({ children }: { children: string }): JSX.Element {
  return <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">{children}</p>
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
          <span className="text-xs text-slate-400">
            {block.instructions.length} instruction{block.instructions.length === 1 ? '' : 's'}
          </span>
        </div>
        <p className="text-xs leading-relaxed text-slate-400">
          Leader: {block.leader_reason || 'first block'} · source {formatLines(block.span)}
        </p>
        {!block.reachable ? (
          <p className="mt-1 font-mono text-xs text-severity-warning">
            unreachable — no path from the entry reaches this block
          </p>
        ) : null}
      </div>

      <div>
        <Heading>Edges in</Heading>
        {incoming.length === 0 ? (
          <p className="font-mono text-xs text-slate-400">none — this is where the function starts</p>
        ) : (
          <ul className="mt-0.5 space-y-0.5 font-mono text-xs text-slate-300">
            {incoming.map((edge) => (
              <li key={`in-${edge.src}-${edge.kind}`} className="truncate">
                <span className="text-slate-100">{edge.src}</span> → here
                <span className="ml-1 text-slate-400">({edge.kind})</span>
                {edge.is_back_edge ? <span className="ml-1 text-severity-warning">· loop</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <Heading>Edges out</Heading>
        {outgoing.length === 0 ? (
          <p className="font-mono text-xs text-slate-400">none — this block ends the path</p>
        ) : (
          <ul className="mt-0.5 space-y-0.5 font-mono text-xs text-slate-300">
            {outgoing.map((edge) => (
              <li key={`out-${edge.dst}-${edge.kind}`} className="truncate">
                here → <span className="text-slate-100">{edge.dst}</span>
                <span className="ml-1 text-slate-400">({edge.kind})</span>
                {edge.is_back_edge ? <span className="ml-1 text-severity-warning">· loop</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      {block.labels.length > 0 ? (
        <div>
          <Heading>Labels</Heading>
          <p className="font-mono text-xs text-slate-100">{block.labels.join(', ')}</p>
        </div>
      ) : null}

      {facts ? (
        <div>
          <Heading>Liveness sets</Heading>
          <div className="mt-0.5 space-y-0.5">
            <LiveSet label="USE" names={facts.use?.[block.id]} reachable={block.reachable} />
            <LiveSet label="DEF" names={facts.def?.[block.id]} reachable={block.reachable} />
            <LiveSet label="IN" names={facts.live_in?.[block.id]} reachable={block.reachable} />
            <LiveSet label="OUT" names={facts.live_out?.[block.id]} reachable={block.reachable} />
          </div>
        </div>
      ) : null}

      <div>
        <Heading>Instructions</Heading>
        {block.instructions.length === 0 ? (
          <p className="font-mono text-xs text-slate-400">empty block</p>
        ) : (
          <ol className="mt-1 space-y-1.5">
            {block.instructions.map((instruction) => (
              <li key={instruction.id} className="border-l border-edge pl-2">
                <p className="font-mono text-xs leading-relaxed text-slate-200">
                  <span className="text-slate-400">{instruction.id}</span>{' '}
                  {instructionSummary(instruction)}
                </p>
                <Facts instruction={instruction} facts={facts?.instructions[instruction.id]} />
              </li>
            ))}
          </ol>
        )}
      </div>

      <div>
        <Heading>Function order</Heading>
        <p className="mt-0.5 font-mono text-xs leading-relaxed text-slate-300">{order.join(' → ')}</p>
      </div>
    </aside>
  )
}
