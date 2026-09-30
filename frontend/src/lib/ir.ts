import type { Instruction, Operand } from '@/api/types'

/**
 * Rendering TAC as text.
 *
 * The backend already emits a formatted `text` field, so the text view uses that verbatim
 * rather than reimplementing the format — a second implementation would drift, and the two
 * would disagree about alignment. What this module owns is the pieces the backend does not
 * provide: an operand's inline form for a table cell, and the classification that drives
 * colouring.
 */

/** One operand, the way it reads inside an instruction. */
export function formatOperand(operand: Operand): string {
  if (operand.kind === 'const') {
    if (operand.type === 'string' || operand.type === 'float') {
      return String(operand.value)
    }
    return String(operand.value ?? '?')
  }
  return operand.name ?? '?'
}

/** Opcode names, grouped by what they do. Drives the row colouring in the TAC table. */
export type InstructionClass = 'compute' | 'move' | 'control' | 'call' | 'io' | 'convert'

export function classify(instruction: Instruction): InstructionClass {
  switch (instruction.opcode) {
    case 'copy':
      return 'move'
    case 'binary':
    case 'unary':
      return 'compute'
    case 'itoi':
    case 'itof':
    case 'ftoi':
    case 'ftof':
      return 'convert'
    case 'goto':
    case 'if_false':
    case 'label':
      return 'control'
    case 'call':
      return 'call'
    case 'print':
    case 'read':
      return 'io'
    default:
      return 'compute'
  }
}

/** Tailwind text colour per class. Kept beside `classify` so the two cannot disagree. */
export const CLASS_COLOURS: Record<InstructionClass, string> = {
  compute: 'text-slate-200',
  move: 'text-slate-300',
  control: 'text-sky-300',
  call: 'text-violet-300',
  io: 'text-emerald-300',
  convert: 'text-amber-300',
}

/** Whether an instruction can trap, read, write, or print. Drives the side-effect markers. */
export function effectFlags(instruction: Instruction): string[] {
  const flags: string[] = []
  if (instruction.has_side_effects) {
    flags.push('side effect')
  }
  if (instruction.may_trap) {
    flags.push('may trap')
  }
  if (instruction.synthetic) {
    flags.push('synthetic')
  }
  return flags
}

/** The destination as text, or an em dash when the instruction writes nothing. */
export function formatDest(instruction: Instruction): string {
  return instruction.dest ? formatOperand(instruction.dest) : '—'
}

/** One line summarizing an instruction, for a block node or a tooltip. */
export function instructionSummary(instruction: Instruction): string {
  const operands = instruction.operands.map(formatOperand).join(', ')
  const format = instruction.format ? `"${instruction.format}" ` : ''
  switch (instruction.opcode) {
    case 'copy':
      return `${formatDest(instruction)} = ${operands}`
    case 'label':
      return `${instruction.target ?? ''}:`
    case 'goto':
      return `goto ${instruction.target ?? '?'}`
    case 'if_false':
      return `if_false ${operands} goto ${instruction.target ?? '?'}`
    case 'call':
      return `${formatDest(instruction)} = call ${instruction.callee ?? '?'}(${operands})`
    case 'print':
      return `print ${format}${operands}`
    case 'read':
      return `read ${format}${operands}`
    case 'return':
      return `return ${operands}`
    default:
      return `${formatDest(instruction)} = ${instruction.opcode} ${operands}`
  }
}
