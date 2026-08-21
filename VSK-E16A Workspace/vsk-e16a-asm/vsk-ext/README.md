# VSK-E16A Assembly (xasm16a) — VS Code Extension

Language support for VSK-E16A / Xasm16A Extended assembly (lowercase-typed).

## Features

### Syntax highlighting
- Registers (`r0`-`r15`, plus `rFLAGS`/`rIP`/`rSP`) in **blue**
- Decimal and hex numeric literals in **green**
- `;` line comments in **dark green**
- `%` directives in a muted **red**
- Label definitions and every place a label is *used* as an operand (`jmp`,
  `call`, `mov` dest/src, etc.) in **yellow**

### Hovers
- Hover any **opcode** to see what it does, how to use it, and its numeric
  opcode value.
- Hover any **label** to see the `;` comment block written directly above
  its definition, rendered as Markdown. Comment collection stops at the
  first non-comment line. Labels more than 100 lines from the cursor are
  skipped unless their comment block starts with `;$$far` (the marker
  itself is never shown in the rendered hover).

### Navigation & refactoring
- **Go To Declaration / Definition** (`F12` / Ctrl+Click) on any label jumps
  straight to where it's defined.
- **Document Outline / breadcrumbs** (`Ctrl+Shift+O`) lists every label in
  the file for quick jumping.
- **Find All References** (`Shift+F12`) lists every place a label is
  defined and used.
- **Rename Symbol** (`F2`) on a label renames it everywhere it's defined
  and referenced in the file.

### Diagnostics
- Jumping/calling/moving to a label that isn't defined anywhere in the file
  is flagged as an **error**.
- Defining the same label twice is flagged as a **warning** on every
  duplicate definition.
- An operand of the wrong shape for its opcode (e.g. `add r1, 0, r1` - `add`
  can't take an immediate) is flagged as an **error** naming what was
  expected.
- An unrecognized `%directive` (e.g. `%include`, which isn't supported) is
  flagged as an **error** listing the known directives.

### Autocomplete
- Typing suggests opcodes and directives (with a short description in the
  suggestion details) as well as any labels already defined in the file.
- IntelliSense's quick-suggestions popup is disabled by default for this
  language so it doesn't compete with Tab-based label/directive estimation
  below; press `Ctrl+Space` any time to bring up suggestions manually.

### Editing
- **Tab-based label indent estimation**: pressing Tab on an empty line
  directly beneath a fresh `label:` line inserts an indentation estimated
  from nearby instruction lines, so code under labels stays aligned.

## Building the .vsix

This extension has no build step. From this folder:

```sh
npm install -g @vscode/vsce
vsce package
```

This produces `vsk-e16a-asm-0.1.0.vsix`, installable via
`Extensions: Install from VSIX...` in VS Code.

## Example

```xasm16a
; $$far
; Prints a greeting to the console using vout
; Uses r0 as scratch
main:
	mov r0, 0x0041
	load r0, "hi"
	vout
	hlt
```

Hovering `main` from anywhere in the file (even 500 lines away, since it's
marked `$$far`) shows the two comment lines above it — but not `$$far`
itself. `F12` on any usage of `main` jumps to this definition; `Shift+F12`
lists every usage; `F2` renames it everywhere.
