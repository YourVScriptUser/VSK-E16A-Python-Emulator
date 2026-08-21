const vscode = require('vscode');

const OPCODES = {
  hlt:   { value: 1,  desc: 'Halts the CPU. Triggers the shutdown/Finally() sequence.', usage: 'hlt' },
  mov:   { value: 4,  desc: 'Moves a value between a register, memory, or immediate, and a register or memory destination. Uses addressing modes for both operands: register, immediate, `[rN]` (memory via register), or `[addr]` (direct memory).', usage: 'mov <dest>, <src>\ne.g. mov r0, 0x10\nmov [r1], r2\nmov [0x2000], r3' },
  vout:  { value: 8,  desc: 'Flushes the extended-mode video memory region (starting at 0xF100) to the terminal, interpreting bytes as ASCII or as color-control codes (130-136).', usage: 'vout' },
  add:   { value: 12, desc: 'dest = regA + regB (16-bit wraparound).', usage: 'add rA, rB, rDest' },
  sub:   { value: 16, desc: 'dest = regA - regB (16-bit wraparound).', usage: 'sub rA, rB, rDest' },
  mul:   { value: 20, desc: 'dest = regA * regB (16-bit wraparound).', usage: 'mul rA, rB, rDest' },
  div:   { value: 24, desc: 'dest = regA // regB (integer division, 16-bit wraparound).', usage: 'div rA, rB, rDest' },
  jmp:   { value: 28, desc: 'Unconditional jump. Sets IP to the target address or label.', usage: 'jmp <label|addr>' },
  call:  { value: 32, desc: 'Pushes the current IP to the stack and jumps to the target label (subroutine call).', usage: 'call <label>' },
  ret:   { value: 36, desc: 'Pops the top of the stack into IP, returning from a call.', usage: 'ret' },
  push:  { value: 40, desc: 'Pushes the value of a register onto the stack.', usage: 'push rN' },
  pop:   { value: 44, desc: 'Pops the top of the stack into a register.', usage: 'pop rN' },
  cmp:   { value: 48, desc: 'Compares two registers, setting the FLAGS register: 1 = equal, 2 = first is higher, 3 = first is lower.', usage: 'cmp rA, rB' },
  je:    { value: 52, desc: 'Jumps to the target if FLAGS == 1 (equal, set by cmp).', usage: 'je <label|addr>' },
  jne:   { value: 56, desc: 'Jumps to the target if FLAGS != 1 and FLAGS != 0xFFFF (not equal).', usage: 'jne <label|addr>' },
  dhi:   { value: 60, desc: 'Disables hardware interrupts (clears the IF flag).', usage: 'dhi' },
  load:  { value: 64, desc: 'Writes a null-terminated ASCII string into memory starting at the address held in the given register.', usage: 'load rN, "text"' },
  inc:   { value: 68, desc: 'Increments a register by 1 (16-bit wraparound).', usage: 'inc rN' },
  dec:   { value: 72, desc: 'Decrements a register by 1 (16-bit wraparound).', usage: 'dec rN' },
  nump:  { value: 76, desc: 'Converts the numeric value in one register to ASCII digits and writes them to memory at the address held in another register.', usage: 'nump rAddr, rValue' },
  ehi:   { value: 78, desc: 'Enables hardware interrupts (sets the IF flag).', usage: 'ehi' },
  in:    { value: 80, desc: 'Pops the top value off the PMIO stack into a register.', usage: 'in rN' },
  int:   { value: 82, desc: 'Raises a software interrupt. Pushes IP to the stack and jumps to the handler address stored in the interrupt vector table entry.', usage: 'int <vector_addr>' },
  jh:    { value: 84, desc: 'Jumps to the target if FLAGS == 2 (higher, set by cmp).', usage: 'jh <label|addr>' },
  jl:    { value: 86, desc: 'Jumps to the target if FLAGS == 3 (lower, set by cmp).', usage: 'jl <label|addr>' },
  clf:   { value: 88, desc: 'Clears the FLAGS register (sets it to 0xFFFF) and resets the triple-fault status.', usage: 'clf' },
  jptr:  { value: 90, desc: 'Jumps to the address stored in the given register.', usage: 'jptr rN' },
  movfs: { value: 92, desc: 'Moves a value from a hardware-reserved pseudo-register into a general register. Valid sources: rFLAGS, rIP, rSP.', usage: 'movfs rDest, rFLAGS|rIP|rSP' },
  movsp: { value: 94, desc: 'Sets the stack pointer (SP) to the value in the given register.', usage: 'movsp rN' },
  out:   { value: 96, desc: 'Pushes a value to the PMIO stack from r2 (with port in r1 and notify flag in r3), notifying the mapped virtual device if the notify flag is set.', usage: 'out' },
  shlt:  { value: 98, desc: 'Soft-halts the CPU: spins until a hardware interrupt is raised (requires IF to be enabled via ehi).', usage: 'shlt' }
};

// Opcodes whose operand(s) can validly be a label reference (jump/call targets).
const LABEL_TAKING_OPCODES = new Set(['jmp', 'call', 'je', 'jne', 'jh', 'jl']);
// mov's src operand can also be a label (resolved to an address at assemble time).
const MOV_LIKE_OPCODES = new Set(['mov']);

const DIRECTIVES = {
  '%region': { desc: 'Sets the assembler write pointer to a fixed address.', usage: '%region 0x0600' },
  '%defineword': { desc: 'Defines one or more raw words (decimal, hex, or string) at the current position.', usage: '%defineword 1, 2, "hi"' },
  '%str': { desc: 'Writes a raw ASCII string directly into memory at a fixed address (no length prefix/terminator).', usage: '%str 0x3000, "text"' },
  '%asc': { desc: 'Writes a single ASCII character word directly into memory at a fixed address.', usage: "%asc 0x3000, 'a'" }
};

const FAR_MARKER = '$$far';
const FAR_RANGE_LIMIT = 100;

// The latest BIOS's Interrupt Vector Table (IVT), used to power hover info
// on INT's operand. INT's operand is always a fixed numeric vector address
// (never a label - see the assembler: `addr = int(operands[0], 0)`), so
// this is looked up by numeric value, independent of how it was written
// (0x01, 1, etc).
const IVT_ENTRIES = [
  { addr: 0x01, category: 'General Purpose', name: 'Clear keyboard buffer', operands: 'No register operands' },
  { addr: 0x02, category: 'General Purpose', name: 'Clear video buffer', operands: 'No register operands' },
  { addr: 0x0E, category: 'General Purpose', name: 'Teletype', operands: 'r0: ascii char' },
  { addr: 0x0F, category: 'General Purpose', name: 'Await enter', operands: 'No register operands' },
  { addr: 0x10, category: 'Hardware', name: 'Disk read', operands: 'r0: disk address', notes: 'Returns the read value in r0' },
  { addr: 0x11, category: 'Hardware', name: 'Disk write', operands: 'r0: disk address\nr1: disk value (what to write)' },
  { addr: 0x12, category: 'Hardware', name: 'Reboot', operands: 'No register operands' },
  { addr: 0xEF, category: 'Hardware interrupts', name: 'Keyboard', operands: '(mapped by emulator)' },
  { addr: 0xFF, category: 'Hardware interrupts', name: 'GPF fault vector', operands: '(mapped by emulator)' }
];
const IVT_BY_ADDR = new Map(IVT_ENTRIES.map(e => [e.addr, e]));

function ivtHoverMarkdown(entry) {
  const md = new vscode.MarkdownString();
  md.appendMarkdown(`**0x${entry.addr.toString(16).toUpperCase().padStart(2, '0')}** _(${entry.category})_ — ${entry.name}\n\n`);
  md.appendCodeblock(entry.operands, 'xasm16a');
  if (entry.notes) {
    md.appendMarkdown(`\n${entry.notes}`);
  }
  return md;
}

// Parses an INT instruction's operand out of a line, returning the numeric
// value and the character range it occupies (so hover can check if the
// cursor is actually over it). Supports 0x-hex or plain decimal, same as
// the assembler's `int(operands[0], 0)`.
function parseIntOperand(lineText) {
  const m = /^(\s*int\s+)(\S+)/i.exec(lineText);
  if (!m) return null;
  const startCol = m[1].length;
  const raw = m[2];
  const endCol = startCol + raw.length;
  let value;
  try {
    value = parseInt(raw, raw.toLowerCase().startsWith('0x') ? 16 : 10);
  } catch (e) {
    return null;
  }
  if (Number.isNaN(value)) return null;
  return { value, range: [startCol, endCol] };
}

function opcodeHoverMarkdown(name, info) {
  const md = new vscode.MarkdownString();
  md.appendMarkdown(`**${name}** \`(opcode ${info.value})\`\n\n`);
  md.appendMarkdown(`${info.desc}\n\n`);
  md.appendCodeblock(info.usage, 'xasm16a');
  return md;
}

function directiveHoverMarkdown(name, info) {
  const md = new vscode.MarkdownString();
  md.appendMarkdown(`**${name}** _(directive)_\n\n`);
  md.appendMarkdown(`${info.desc}\n\n`);
  md.appendCodeblock(info.usage, 'xasm16a');
  return md;
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function findLabelDefinitionLine(document, labelName) {
  const re = new RegExp('^\\s*' + escapeRegExp(labelName) + '\\s*:');
  for (let i = 0; i < document.lineCount; i++) {
    if (re.test(document.lineAt(i).text)) return i;
  }
  return -1;
}

// Collects the contiguous ';' comment block directly above a label
// definition line. Stops at the first non-comment (or blank) line.
// Returns { lines, isFar } — isFar true if the first comment line
// (closest to the top of the block) is exactly "$$far".
function collectLabelComments(document, labelLine) {
  const raw = [];
  let i = labelLine - 1;
  while (i >= 0) {
    const text = document.lineAt(i).text;
    const trimmed = text.trim();
    if (trimmed === '') break;
    if (trimmed.startsWith(';')) {
      raw.unshift(trimmed.replace(/^;\s?/, ''));
      i--;
      continue;
    }
    break;
  }

  let isFar = false;
  if (raw.length > 0 && raw[0].trim().toLowerCase() === FAR_MARKER) {
    isFar = true;
    raw.shift();
  }

  return { lines: raw, isFar };
}

const OPCODE_NAMES = Object.keys(OPCODES);
const DIRECTIVE_NAMES = Object.keys(DIRECTIVES);
const REGISTER_RE = /^r(1[0-5]|[0-9])$/;
const PSEUDO_REG_RE = /^(rFLAGS|rIP|rSP)$/;

// Label names may be "namespaced" with dots, e.g. "interrupts.clear_kbuf" -
// one or more [A-Za-z_][A-Za-z0-9_]* segments joined by single dots. This
// fragment is reused everywhere a label name needs to be recognized as one
// whole token (definitions, references, hovers, rename, etc.) so all of
// those places stay in sync.
const LABEL_SEGMENT = '[A-Za-z_][A-Za-z0-9_]*';
const LABEL_NAME_SRC = LABEL_SEGMENT + '(?:\\.' + LABEL_SEGMENT + ')*';
const LABEL_NAME_RE = new RegExp('^' + LABEL_NAME_SRC + '$');

const LABEL_DEF_RE = new RegExp('^\\s*(' + LABEL_NAME_SRC + ')\\s*:');
// Matches a whole line that is *only* a label definition (optionally with
// trailing whitespace) - used by the tab-indent estimator to detect
// "we're right under a fresh label:" and to skip over other label lines
// while scanning upward for an indentation reference.
const LABEL_ONLY_LINE_RE = new RegExp('^\\s*' + LABEL_NAME_SRC + '\\s*:\\s*$');
const IDENT_RE = new RegExp(LABEL_NAME_SRC, 'g');
// Word-range regex for hover/definition/rename lookups - same shape, used
// with document.getWordRangeAtPosition (no anchors, no global flag).
const WORD_RANGE_RE = new RegExp(LABEL_NAME_SRC);
const WORD_RANGE_WITH_DIRECTIVE_RE = new RegExp('%?' + LABEL_NAME_SRC);

// Replaces the contents of every "..." / '...' string literal on a line
// with spaces (same length, quotes included), so downstream scanning never
// mistakes the words *inside* a string ("Initializing hardware...") for
// bare identifiers/label references. Column positions of everything else
// on the line are preserved since the replacement is equal-length.
function maskStringLiterals(text) {
  let result = '';
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === '"' || c === "'") {
      const quote = c;
      let j = i + 1;
      while (j < text.length && text[j] !== quote) j++;
      const hasClosingQuote = j < text.length;
      result += quote; // keep opening quote
      result += ' '.repeat(j - (i + 1)); // blank the interior only
      if (hasClosingQuote) {
        result += quote; // keep closing quote
        i = j + 1;
      } else {
        i = j; // unterminated string - nothing left to keep
      }
    } else {
      result += c;
      i++;
    }
  }
  return result;
}

// Returns a Map<labelName, {line, range}> of every label definition in the document.
function findAllLabelDefinitions(document) {
  const defs = new Map();
  for (let i = 0; i < document.lineCount; i++) {
    const text = document.lineAt(i).text;
    const m = LABEL_DEF_RE.exec(text);
    if (m) {
      const name = m[1];
      const startCol = m[0].indexOf(name);
      const range = new vscode.Range(i, startCol, i, startCol + name.length);
      if (!defs.has(name)) defs.set(name, []);
      defs.get(name).push({ line: i, range });
    }
  }
  return defs;
}

// Returns every bare-identifier occurrence in the document that is not a
// label definition, not an opcode, not a directive, and not a register -
// i.e. every place a label is *used*. Array of {name, range, line}.
function findAllLabelReferences(document) {
  const refs = [];
  for (let i = 0; i < document.lineCount; i++) {
    const text = document.lineAt(i).text;
    // Mask out string literal contents first (equal-length, so column
    // positions for everything else on the line stay correct), so a ';'
    // inside a string isn't mistaken for a comment start, and so words
    // inside strings are never scanned as label references below.
    const masked = maskStringLiterals(text);
    const commentIdx = masked.indexOf(';');
    const codeText = commentIdx === -1 ? masked : masked.substring(0, commentIdx);

    const defMatch = LABEL_DEF_RE.exec(text);
    const defName = defMatch ? defMatch[1] : null;
    const defCol = defMatch ? defMatch[0].indexOf(defName) : -1;

    IDENT_RE.lastIndex = 0;
    let m;
    while ((m = IDENT_RE.exec(codeText)) !== null) {
      const word = m[0];
      const col = m.index;

      // skip the label definition token itself
      if (defName && col === defCol && word === defName) continue;

      // Skip identifier-shaped matches that are actually the tail of a
      // longer alphanumeric token, most commonly the "x00EF" tail of a
      // hex literal "0x00EF" - IDENT_RE can only start matching at 'x'
      // there since '0' isn't a valid identifier-start char, producing a
      // spurious match. If the character right before this match is a
      // digit, it's not a real standalone identifier - skip it.
      const precedingChar = col > 0 ? codeText[col - 1] : '';
      if (/[0-9]/.test(precedingChar)) continue;

      // Skip directive names ("%defineword" etc.) - IDENT_RE never
      // includes the leading "%" since "%" isn't a word character, so
      // `word` here is just "defineword", not "%defineword". Check the
      // actual preceding character instead of testing `word` itself.
      if (precedingChar === '%') continue;

      const lower = word.toLowerCase();
      if (OPCODE_NAMES.includes(lower)) continue;
      if (REGISTER_RE.test(word) || PSEUDO_REG_RE.test(word)) continue;
      if (DIRECTIVE_NAMES.includes('%' + lower)) continue;

      refs.push({
        name: word,
        line: i,
        range: new vscode.Range(i, col, i, col + word.length)
      });
    }
  }
  return refs;
}

// ---------------------------------------------------------------------------
// Operand validation
//
// Each opcode's valid operand "shapes" per position. Kinds:
//   reg       -> r0-r15
//   pseudoreg -> rFLAGS / rIP / rSP (MOVFS source only)
//   imm       -> decimal or hex literal
//   memreg    -> [rN]
//   memaddr   -> [addr] (immediate address in brackets)
//   label     -> bare identifier (jmp/call/je/jne/jh/jl targets, or a mov
//                operand resolved to an address at assemble time)
//   addr      -> bare decimal/hex literal used as a raw address (int's operand)
//   any-mov-dest -> reg | memreg | memaddr           (mov dest only)
//   any-mov-src   -> reg | imm | memreg | memaddr | label   (mov src only)
// A position can allow multiple kinds; operand not matching any of them is
// invalid.
// ---------------------------------------------------------------------------

const OPERAND_SPECS = {
  hlt:   [],
  mov:   [['any-mov-dest'], ['any-mov-src']],
  vout:  [],
  add:   [['reg'], ['reg'], ['reg']],
  sub:   [['reg'], ['reg'], ['reg']],
  mul:   [['reg'], ['reg'], ['reg']],
  div:   [['reg'], ['reg'], ['reg']],
  jmp:   [['label', 'addr']],
  call:  [['label']],
  ret:   [],
  push:  [['reg']],
  pop:   [['reg']],
  cmp:   [['reg'], ['reg']],
  je:    [['label', 'addr']],
  jne:   [['label', 'addr']],
  dhi:   [],
  load:  [['reg'], ['string']],
  inc:   [['reg']],
  dec:   [['reg']],
  nump:  [['reg'], ['reg']],
  ehi:   [],
  in:    [['reg']],
  int:   [['addr']],
  jh:    [['label', 'addr']],
  jl:    [['label', 'addr']],
  clf:   [],
  jptr:  [['reg']],
  movfs: [['reg'], ['pseudoreg']],
  movsp: [['reg']],
  out:   [],
  shlt:  []
};

const OPCODE_DISPLAY_NAME = {
  add: 'add', sub: 'sub', mul: 'mul', div: 'div'
};

function classifyOperand(raw) {
  const s = raw.trim();
  if (s === '') return { kinds: [] };

  if (REGISTER_RE.test(s)) return { kinds: ['reg'] };
  if (PSEUDO_REG_RE.test(s)) return { kinds: ['pseudoreg'] };

  if (/^0[xX][0-9a-fA-F]+$/.test(s) || /^[0-9]+$/.test(s)) {
    return { kinds: ['imm', 'addr'] };
  }

  if (/^\[\s*r(1[0-5]|[0-9])\s*\]$/.test(s)) return { kinds: ['memreg'] };
  if (/^\[\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*\]$/.test(s)) return { kinds: ['memaddr'] };

  if (/^"[^"]*"$/.test(s) || /^'[^']*'$/.test(s)) return { kinds: ['string'] };

  if (LABEL_NAME_RE.test(s)) return { kinds: ['label'] };

  return { kinds: [] }; // unrecognized shape entirely
}

function kindAllowed(allowedList, actualKinds) {
  for (const allowed of allowedList) {
    if (allowed === 'any-mov-dest') {
      if (actualKinds.some(k => ['reg', 'memreg', 'memaddr'].includes(k))) return true;
    } else if (allowed === 'any-mov-src') {
      if (actualKinds.some(k => ['reg', 'imm', 'memreg', 'memaddr', 'label'].includes(k))) return true;
    } else if (actualKinds.includes(allowed)) {
      return true;
    }
  }
  return false;
}

const KIND_LABELS = {
  reg: 'a register (rN)',
  pseudoreg: 'a pseudo-register (rFLAGS/rIP/rSP)',
  imm: 'an immediate value',
  memreg: 'a memory reference [rN]',
  memaddr: 'a memory reference [addr]',
  label: 'a label',
  addr: 'an address',
  string: 'a string',
  'any-mov-dest': 'a register or memory reference',
  'any-mov-src': 'a register, immediate, memory reference, or label'
};

function describeAllowed(allowedList) {
  return allowedList.map(k => KIND_LABELS[k] || k).join(' or ');
}

// Splits an operand list on top-level commas (ignores commas inside [] / "" / '').
function splitOperands(text) {
  const parts = [];
  let depth = 0;
  let inStr = null;
  let cur = '';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inStr) {
      cur += ch;
      if (ch === inStr) inStr = null;
      continue;
    }
    if (ch === '"' || ch === "'") { inStr = ch; cur += ch; continue; }
    if (ch === '[') { depth++; cur += ch; continue; }
    if (ch === ']') { depth--; cur += ch; continue; }
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue; }
    cur += ch;
  }
  if (cur.trim() !== '') parts.push(cur);
  return parts.map(p => p.trim()).filter(p => p !== '');
}

// Validates operand shapes for every instruction line in the document.
// Returns an array of {range, message}.
function findOperandDiagnostics(document) {
  const problems = [];

  for (let i = 0; i < document.lineCount; i++) {
    const rawLine = document.lineAt(i).text;
    const maskedLine = maskStringLiterals(rawLine);
    const commentIdx = maskedLine.indexOf(';');
    const codeText = commentIdx === -1 ? maskedLine : maskedLine.substring(0, commentIdx);
    const trimmed = codeText.trim();
    if (trimmed === '') continue;
    if (LABEL_DEF_RE.test(codeText) && !/\S/.test(codeText.replace(LABEL_DEF_RE, ''))) continue;
    if (trimmed.startsWith('%')) {
      // Unknown-directive check: the operand grammar for known directives
      // is handled separately (they're free-form), but an unrecognized
      // directive name is always an error.
      const dirMatch = trimmed.match(/^%([A-Za-z]*)/);
      if (dirMatch) {
        const dirName = '%' + dirMatch[1].toLowerCase();
        if (!DIRECTIVE_NAMES.includes(dirName)) {
          const col = rawLine.indexOf('%');
          const tokenLen = 1 + dirMatch[1].length;
          const range = new vscode.Range(i, col, i, col + tokenLen);
          problems.push({
            range,
            message: `Error: "%${dirMatch[1]}" is not a known directive! Known directives: ${DIRECTIVE_NAMES.join(', ')}.`
          });
        }
      }
      continue; // directives otherwise have their own operand grammar
    }

    const opMatch = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$/);
    if (!opMatch) continue;
    const opword = opMatch[1];
    const lower = opword.toLowerCase();
    if (!(lower in OPERAND_SPECS)) continue; // not a recognized opcode - leave to other diagnostics

    // column offset of the opcode word, used as a fallback range anchor
    const opStartCol = rawLine.toLowerCase().indexOf(lower, 0);

    const rest = opMatch[2];
    const spec = OPERAND_SPECS[lower];

    // Special-case: `out` takes NO operands - r1/r2/r3 are fixed implicit
    // pseudo-operands (set via prior `mov`), never written inline.
    if (lower === 'out' && rest.trim() !== '') {
      const restCol = rawLine.indexOf(rest, opStartCol);
      const range = new vscode.Range(i, restCol >= 0 ? restCol : 0, i, rawLine.length);
      problems.push({
        range,
        message: `Error: out takes no operands! Port/value/notify are fixed pseudo-operands - set r1 (port), r2 (value), r3 (notify) with mov beforehand, then call "out" alone.`
      });
      continue;
    }

    const operandTexts = rest.trim() === '' ? [] : splitOperands(rest);

    if (operandTexts.length !== spec.length) {
      const range = new vscode.Range(i, opStartCol, i, rawLine.length);
      problems.push({
        range,
        message: `Error: ${lower} expects ${spec.length} operand${spec.length === 1 ? '' : 's'}, got ${operandTexts.length}!`
      });
      continue;
    }

    for (let idx = 0; idx < spec.length; idx++) {
      const allowedKinds = spec[idx];
      const operandText = operandTexts[idx];
      const { kinds } = classifyOperand(operandText);

      if (kinds.length === 0 || !kindAllowed(allowedKinds, kinds)) {
        // locate this operand's column within the raw line
        const searchFrom = rawLine.indexOf(rest, opStartCol);
        let col = searchFrom >= 0 ? rawLine.indexOf(operandText, searchFrom) : -1;
        if (col === -1) col = opStartCol;
        const range = new vscode.Range(i, col, i, col + operandText.length);

        let kindWord = 'value';
        if (kinds.includes('imm')) kindWord = 'immediate';
        else if (kinds.includes('reg')) kindWord = 'register';
        else if (kinds.includes('label')) kindWord = 'label';
        else if (kinds.includes('memreg') || kinds.includes('memaddr')) kindWord = 'memory reference';
        else if (kinds.includes('string')) kindWord = 'string';
        else if (kinds.includes('pseudoreg')) kindWord = 'pseudo-register';

        const capitalized = lower.charAt(0).toUpperCase() + lower.slice(1);
        problems.push({
          range,
          message: `Error: ${capitalized} cannot take ${kindWord}! Expected ${describeAllowed(allowedKinds)}.`
        });
      }
    }
  }

  return problems;
}

function activate(context) {
  const hoverProvider = vscode.languages.registerHoverProvider('xasm16a', {
    provideHover(document, position) {
      // ---- INT operand hover: look up the numeric vector against the IVT ----
      const lineText = document.lineAt(position.line).text;
      const intOperand = parseIntOperand(lineText);
      if (intOperand && position.character >= intOperand.range[0] && position.character <= intOperand.range[1]) {
        const entry = IVT_BY_ADDR.get(intOperand.value);
        const range = new vscode.Range(position.line, intOperand.range[0], position.line, intOperand.range[1]);
        if (entry) {
          return new vscode.Hover(ivtHoverMarkdown(entry), range);
        }
        const md = new vscode.MarkdownString();
        md.appendMarkdown(`_Interrupt vector \`0x${intOperand.value.toString(16).toUpperCase()}\` is not in the known IVT._`);
        return new vscode.Hover(md, range);
      }

      const wordRange = document.getWordRangeAtPosition(position, WORD_RANGE_WITH_DIRECTIVE_RE);
      if (!wordRange) return;
      const word = document.getText(wordRange);
      const lower = word.toLowerCase();

      if (OPCODES[lower]) {
        return new vscode.Hover(opcodeHoverMarkdown(lower, OPCODES[lower]), wordRange);
      }

      if (DIRECTIVES[lower]) {
        return new vscode.Hover(directiveHoverMarkdown(lower, DIRECTIVES[lower]), wordRange);
      }

      const labelLine = findLabelDefinitionLine(document, word);
      if (labelLine !== -1) {
        const { lines, isFar } = collectLabelComments(document, labelLine);

        // Range gating: ignore (don't hover) labels more than 100 lines
        // away from the cursor, unless the label's comment block is
        // marked "$$far".
        const distance = Math.abs(position.line - labelLine);
        if (distance > FAR_RANGE_LIMIT && !isFar) {
          return undefined;
        }

        const md = new vscode.MarkdownString();
        md.appendMarkdown(`**${word}** _(label)_ — defined at line ${labelLine + 1}\n\n`);
        if (lines.length > 0) {
          md.appendMarkdown('---\n\n');
          md.appendMarkdown(lines.join('\n\n'));
        } else {
          md.appendMarkdown('_(no comment block above this label)_');
        }
        return new vscode.Hover(md, wordRange);
      }

      return undefined;
    }
  });

  // ---- Go To Declaration / Definition ----
  const definitionProvider = vscode.languages.registerDefinitionProvider('xasm16a', {
    provideDefinition(document, position) {
      const wordRange = document.getWordRangeAtPosition(position, WORD_RANGE_RE);
      if (!wordRange) return;
      const word = document.getText(wordRange);
      const defs = findAllLabelDefinitions(document);
      const hits = defs.get(word);
      if (!hits || hits.length === 0) return;
      return hits.map(h => new vscode.Location(document.uri, h.range));
    }
  });

  const declarationProvider = vscode.languages.registerDeclarationProvider('xasm16a', {
    provideDeclaration(document, position) {
      const wordRange = document.getWordRangeAtPosition(position, WORD_RANGE_RE);
      if (!wordRange) return;
      const word = document.getText(wordRange);
      const defs = findAllLabelDefinitions(document);
      const hits = defs.get(word);
      if (!hits || hits.length === 0) return;
      return hits.map(h => new vscode.Location(document.uri, h.range));
    }
  });

  // ---- Document Symbols (outline / breadcrumbs) ----
  const symbolProvider = vscode.languages.registerDocumentSymbolProvider('xasm16a', {
    provideDocumentSymbols(document) {
      const defs = findAllLabelDefinitions(document);
      const symbols = [];
      for (const [name, hits] of defs.entries()) {
        for (const h of hits) {
          const lineRange = document.lineAt(h.line).range;
          symbols.push(new vscode.DocumentSymbol(
            name,
            '',
            vscode.SymbolKind.Function,
            lineRange,
            h.range
          ));
        }
      }
      symbols.sort((a, b) => a.range.start.line - b.range.start.line);
      return symbols;
    }
  });

  // ---- Find All References ----
  const referenceProvider = vscode.languages.registerReferenceProvider('xasm16a', {
    provideReferences(document, position, context2) {
      const wordRange = document.getWordRangeAtPosition(position, WORD_RANGE_RE);
      if (!wordRange) return [];
      const word = document.getText(wordRange);

      const locations = [];
      const defs = findAllLabelDefinitions(document);
      if (context2.includeDeclaration && defs.has(word)) {
        for (const h of defs.get(word)) {
          locations.push(new vscode.Location(document.uri, h.range));
        }
      }
      const refs = findAllLabelReferences(document);
      for (const r of refs) {
        if (r.name === word) {
          locations.push(new vscode.Location(document.uri, r.range));
        }
      }
      return locations;
    }
  });

  // ---- Rename Symbol ----
  const renameProvider = vscode.languages.registerRenameProvider('xasm16a', {
    prepareRename(document, position) {
      const wordRange = document.getWordRangeAtPosition(position, WORD_RANGE_RE);
      if (!wordRange) {
        throw new Error('No renameable symbol here.');
      }
      const word = document.getText(wordRange);
      const lower = word.toLowerCase();
      if (OPCODE_NAMES.includes(lower) || REGISTER_RE.test(word) || PSEUDO_REG_RE.test(word)) {
        throw new Error('Only labels can be renamed.');
      }
      return wordRange;
    },
    provideRenameEdits(document, position, newName) {
      const wordRange = document.getWordRangeAtPosition(position, WORD_RANGE_RE);
      if (!wordRange) return;
      const word = document.getText(wordRange);
      if (!LABEL_NAME_RE.test(newName)) {
        vscode.window.showErrorMessage('Label names must start with a letter or underscore.');
        return;
      }

      const edit = new vscode.WorkspaceEdit();
      const defs = findAllLabelDefinitions(document);
      if (defs.has(word)) {
        for (const h of defs.get(word)) {
          edit.replace(document.uri, h.range, newName);
        }
      }
      const refs = findAllLabelReferences(document);
      for (const r of refs) {
        if (r.name === word) {
          edit.replace(document.uri, r.range, newName);
        }
      }
      return edit;
    }
  });

  // ---- Diagnostics: undefined label refs + duplicate label definitions ----
  const diagnosticCollection = vscode.languages.createDiagnosticCollection('xasm16a');
  context.subscriptions.push(diagnosticCollection);

  function refreshDiagnostics(document) {
    if (document.languageId !== 'xasm16a') return;

    const diagnostics = [];
    const defs = findAllLabelDefinitions(document);

    // duplicate label definitions
    for (const [name, hits] of defs.entries()) {
      if (hits.length > 1) {
        for (const h of hits) {
          diagnostics.push(new vscode.Diagnostic(
            h.range,
            `Duplicate label "${name}" is defined ${hits.length} times in this file.`,
            vscode.DiagnosticSeverity.Warning
          ));
        }
      }
    }

    // undefined label references
    const refs = findAllLabelReferences(document);
    for (const r of refs) {
      if (!defs.has(r.name)) {
        diagnostics.push(new vscode.Diagnostic(
          r.range,
          `"${r.name}" is not a defined label in this file.`,
          vscode.DiagnosticSeverity.Error
        ));
      }
    }

    // invalid operand shapes per opcode
    const operandProblems = findOperandDiagnostics(document);
    for (const p of operandProblems) {
      diagnostics.push(new vscode.Diagnostic(
        p.range,
        p.message,
        vscode.DiagnosticSeverity.Error
      ));
    }

    diagnosticCollection.set(document.uri, diagnostics);
  }

  if (vscode.window.activeTextEditor) {
    refreshDiagnostics(vscode.window.activeTextEditor.document);
  }
  context.subscriptions.push(vscode.workspace.onDidOpenTextDocument(refreshDiagnostics));
  context.subscriptions.push(vscode.workspace.onDidChangeTextDocument(e => refreshDiagnostics(e.document)));
  context.subscriptions.push(vscode.window.onDidChangeActiveTextEditor(editor => {
    if (editor) refreshDiagnostics(editor.document);
  }));
  context.subscriptions.push(vscode.workspace.onDidCloseTextDocument(doc => diagnosticCollection.delete(doc.uri)));

  // ---- Label autocomplete only ----
  const completionProvider = vscode.languages.registerCompletionItemProvider(
    'xasm16a',
    {
      provideCompletionItems(document, position) {
        // Don't suggest labels while typing inside a ';' comment - a
        // semicolon anywhere before the cursor on this line means
        // everything from there onward is comment text.
        const linePrefix = document.lineAt(position.line).text.substring(0, position.character);
        if (linePrefix.includes(';')) {
          return [];
        }

        // Don't suggest labels while the cursor is still positioned within
        // the opcode/first-token itself (nothing typed yet but optional
        // leading whitespace + a partial/complete word, no space after
        // it) - e.g. typing "mo" or "mov" with the cursor still inside
        // that word. This is what was swallowing "mov" -> Enter and
        // replacing it with a label suggestion.
        if (/^\s*[A-Za-z_][A-Za-z0-9_]*$/.test(linePrefix)) {
          return [];
        }

        // Only suggest labels for opcodes whose operands can actually
        // resolve to a label at assemble time (mov's dest/src, and the
        // various jump/call targets). Every other opcode only takes
        // registers/immediates, so a label there is always wrong.
        const opcodeMatch = /^\s*([A-Za-z_][A-Za-z0-9_]*)/.exec(linePrefix);
        const opcode = opcodeMatch ? opcodeMatch[1].toLowerCase() : null;
        if (!opcode || !(LABEL_TAKING_OPCODES.has(opcode) || MOV_LIKE_OPCODES.has(opcode))) {
          return [];
        }

        // A label reference is never valid inside "[...]" (memory-address
        // operands there must be numeric/register, per the assembler) -
        // if there's an unclosed '[' before the cursor, don't suggest.
        const openBrackets = (linePrefix.match(/\[/g) || []).length;
        const closeBrackets = (linePrefix.match(/\]/g) || []).length;
        if (openBrackets > closeBrackets) {
          return [];
        }

        const items = [];

        // suggest known labels only, handy for jmp/call/mov targets
        const defs = findAllLabelDefinitions(document);
        for (const name of defs.keys()) {
          const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Reference);
          item.detail = 'label';
          item.sortText = '0_' + name;
          items.push(item);
        }

        return items;
      }
    },
    ' ', ',' // trigger characters: fire the widget right after "jmp ", "mov r0, " etc.
             // regardless of the user's editor.quickSuggestions setting.
  );


  const tabCommand = vscode.commands.registerTextEditorCommand(
    'vsk-e16a-asm.smartTabIndent',
    (editor, edit) => {
      const doc = editor.document;
      const sel = editor.selection;

      if (!sel.isEmpty || doc.languageId !== 'xasm16a') {
        vscode.commands.executeCommand('tab');
        return;
      }

      const line = sel.active.line;
      const lineText = doc.lineAt(line).text;
      const beforeCursor = lineText.substring(0, sel.active.character);

      if (beforeCursor.trim() !== '') {
        vscode.commands.executeCommand('tab');
        return;
      }

      const prevLine = line > 0 ? doc.lineAt(line - 1).text : '';
      const isPrevLabel = LABEL_ONLY_LINE_RE.test(prevLine);

      if (!isPrevLabel) {
        vscode.commands.executeCommand('tab');
        return;
      }

      const tabSize = editor.options.tabSize || 4;
      const indentUnit = typeof tabSize === 'number' ? tabSize : 4;

      let estimated = null;
      for (let i = line - 1; i >= 0 && i >= line - 40; i--) {
        const t = doc.lineAt(i).text;
        if (LABEL_ONLY_LINE_RE.test(t)) continue;
        if (t.trim() === '') continue;
        const match = t.match(/^(\s+)\S/);
        if (match) {
          estimated = match[1];
          break;
        }
      }

      const indentStr = estimated !== null ? estimated : ' '.repeat(indentUnit);
      edit.insert(sel.active, indentStr);
    }
  );

  context.subscriptions.push(
    hoverProvider,
    tabCommand,
    definitionProvider,
    declarationProvider,
    symbolProvider,
    referenceProvider,
    renameProvider,
    completionProvider
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
