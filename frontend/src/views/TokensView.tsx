import type { JSX } from 'react'

import { tokensOf, stageSkipped } from '@/api/client'
import type { Token } from '@/api/types'
import { formatLines, formatSpan } from '@/lib/span'
import { useCompilerStore } from '@/store/compiler'
import { Count, EmptyState, Panel } from '@/components/ui'

/**
 * The token stream, in order.
 *
 * A flat table rather than a tree, because that is what the lexer produces and because the
 * useful question here is "what did it see, in what order" — which a table answers and a tree
 * hides. Clicking a token selects its span, which is how a user ties a token back to the text
 * they typed.
 */

/** Token categories get a colour so a string is distinguishable from an identifier at a glance. */
const CATEGORY_COLOURS: Record<string, string> = {
  keyword: 'text-violet-300',
  identifier: 'text-slate-200',
  literal: 'text-emerald-300',
  string: 'text-amber-300',
  operator: 'text-sky-300',
  delimiter: 'text-slate-400',
  comment: 'text-slate-500 italic',
}

export function TokensView(): JSX.Element {
  const response = useCompilerStore((state) => state.response)
  const select = useCompilerStore((state) => state.select)

  if (!response) {
    return (
      <Panel title="Tokens" subtitle="Waiting for the first compile.">
        <EmptyState title="Nothing to show yet" detail="Type a program and the token stream will appear here." />
      </Panel>
    )
  }

  const tokens = tokensOf(response)

  if (!tokens) {
    return (
      <Panel title="Tokens" subtitle="The lexer did not run.">
        <EmptyState
          title="No tokens"
          detail="The lexer was skipped, so there is no token stream to show. This happens when an earlier stage reported an error."
          skipped={stageSkipped(response, 'tokens')}
        />
      </Panel>
    )
  }

  const errors = tokens.filter((token) => token.category === 'error')

  return (
    <Panel
      title="Tokens"
      subtitle="The lexer's output, in source order. Select a token to highlight where it came from."
      actions={
        <div className="flex items-center gap-2">
          <Count label={`${tokens.length} tokens`}>{tokens.length} tokens</Count>
          {errors.length > 0 ? (
            <span className="font-mono text-2xs text-severity-error">{errors.length} invalid</span>
          ) : null}
        </div>
      }
    >
      {tokens.length === 0 ? (
        <EmptyState title="No tokens" detail="The program is empty or contains only whitespace." />
      ) : (
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-surface-raised text-left text-2xs uppercase tracking-wider text-slate-500">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">#</th>
              <th scope="col" className="px-3 py-2 font-medium">Type</th>
              <th scope="col" className="px-3 py-2 font-medium">Lexeme</th>
              <th scope="col" className="px-3 py-2 font-medium">Category</th>
              <th scope="col" className="px-3 py-2 font-medium">Location</th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((token: Token, index: number) => (
              <tr
                key={`${token.span.start_line}:${token.span.start_col}:${index}`}
                onClick={() =>
                  select({
                    line: token.span.start_line,
                    endLine: token.span.end_line,
                    origin: `token ${index}`,
                  })
                }
                className={`cursor-pointer border-t border-edge/60 hover:bg-surface-overlay ${
                  token.category === 'error' ? 'bg-severity-error/10' : ''
                }`}
              >
                <td className="px-3 py-1 font-mono text-slate-400">{index + 1}</td>
                <td className="px-3 py-1 font-mono text-slate-400">{token.type}</td>
                <td className={`px-3 py-1 font-mono ${CATEGORY_COLOURS[token.category] ?? 'text-slate-200'}`}>
                  {token.lexeme === '\n' ? '\\n' : token.lexeme === ' ' ? '␠' : token.lexeme}
                </td>
                <td className="px-3 py-1 font-mono text-slate-500">{token.category}</td>
                <td className="whitespace-nowrap px-3 py-1 font-mono text-slate-500" title={formatSpan(token.span)}>
                  {formatLines(token.span)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
