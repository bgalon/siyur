import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * T-06 — the RTL property, protected before it is spent.
 *
 * The UX audit measured something worth keeping: **zero physical direction
 * properties against 35 logical ones** across the whole stylesheet set. RTL is
 * deferred to M3, but the debt is not accumulating, and that is only true for as
 * long as nobody writes `margin-left`. This test is what makes it stay true.
 *
 * Unlike the rest of the DU-06a gate, **this one passes on arrival.** It is not a
 * finding; it is a ratchet. A gate that only ever lands red teaches that red is
 * normal — this is the other half, and it is deliberately cheap (a source scan, no
 * browser, single-digit milliseconds).
 *
 * ## Comments are stripped first
 *
 * `web/src/library.css` documents its own rule in prose that names `margin-left`.
 * The patterns here all require a `:` after the property, so that particular line
 * would survive them anyway — but a checker whose correctness depends on how a
 * comment happens to be punctuated is one edit away from a false red on the
 * stylesheet that best follows the rule. Comment text is blanked to spaces rather
 * than deleted, so reported line numbers still point at the real line.
 *
 * ## What "zero physical direction properties" is a claim about
 *
 * Longhands **and** the asymmetric shorthands that hide the same decision. Scanning
 * only longhands left three declarations standing — `padding: 6px 8px 6px 14px`,
 * `padding: 0 0 0 14px`, `border-radius: 0 8px 8px 0` — each sitting next to a
 * `border-inline-start` that had already been converted, which is what an oversight
 * looks like rather than a decision. All three are converted in this commit, so the
 * claim and the measurement now agree.
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

/**
 * Physical direction properties, each paired with the logical property that
 * replaces it — the message has to teach the fix, not just refuse the code.
 */
const PHYSICAL: readonly (readonly [RegExp, string])[] = [
  [/(?:^|[;{\s])margin-left\s*:/gi, 'margin-inline-start'],
  [/(?:^|[;{\s])margin-right\s*:/gi, 'margin-inline-end'],
  [/(?:^|[;{\s])padding-left\s*:/gi, 'padding-inline-start'],
  [/(?:^|[;{\s])padding-right\s*:/gi, 'padding-inline-end'],
  [/(?:^|[;{\s])border-left(?:-\w+)?\s*:/gi, 'border-inline-start'],
  [/(?:^|[;{\s])border-right(?:-\w+)?\s*:/gi, 'border-inline-end'],
  [/(?:^|[;{\s])left\s*:/gi, 'inset-inline-start'],
  [/(?:^|[;{\s])right\s*:/gi, 'inset-inline-end'],
  [/(?:^|[;{\s])border-top-left-radius\s*:/gi, 'border-start-start-radius'],
  [/(?:^|[;{\s])border-top-right-radius\s*:/gi, 'border-start-end-radius'],
  [/(?:^|[;{\s])border-bottom-left-radius\s*:/gi, 'border-end-start-radius'],
  [/(?:^|[;{\s])border-bottom-right-radius\s*:/gi, 'border-end-end-radius'],
  [/text-align\s*:\s*(?:left|right)\b/gi, 'text-align: start / end'],
  [/float\s*:\s*(?:left|right)\b/gi, 'float: inline-start / inline-end'],
  [/clear\s*:\s*(?:left|right)\b/gi, 'clear: inline-start / inline-end'],
]

/**
 * Asymmetric shorthands are direction-dependent too, and a longhand-only scan lets
 * them through — `padding: 6px 8px 6px 14px`, `padding: 0 0 0 14px` and
 * `border-radius: 0 8px 8px 0` all name physical edges and all must change for the
 * M3 RTL pass. Missing them would make "zero physical direction properties" a claim
 * about the regexes rather than about the stylesheet.
 *
 * Counted per declaration rather than matched with one big pattern: a regex that
 * tries to count value tokens across a whole file backtracks into neighbouring
 * declarations and reports `margin: 0 0 4px` as four values. Tokenising is both
 * simpler and right.
 *
 * A **4-value** box shorthand names its inline edges separately; 1–3 values do not,
 * so `margin: 0 auto`, `margin: 0 0 4px` and `padding: 10px` all stay legal.
 * `border-radius` is flagged from **2** values, where the corners stop being equal.
 */
const DECLARATION = /([-a-z]+)\s*:\s*([^;{}]+)/gi

/** Whitespace-separated values, treating `rgb(0 0 0 / 10%)` and `var(--x)` as one. */
function valueTokens(value: string): string[] {
  const tokens: string[] = []
  let depth = 0
  let current = ''
  for (const ch of value.trim()) {
    if (ch === '(') depth += 1
    if (ch === ')') depth -= 1
    if (depth === 0 && /\s/.test(ch)) {
      if (current) tokens.push(current)
      current = ''
      continue
    }
    current += ch
  }
  if (current) tokens.push(current)
  return tokens
}

/** Replace every `/* … *​/` comment with newlines, preserving line numbering. */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '))
}

function cssFiles(dir: string): string[] {
  const found: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) found.push(...cssFiles(full))
    else if (entry.endsWith('.css')) found.push(full)
  }
  return found.sort()
}

interface Violation {
  readonly file: string
  readonly line: number
  readonly text: string
  readonly use: string
}

function scan(file: string): Violation[] {
  const source = stripComments(readFileSync(file, 'utf8'))
  const lines = source.split('\n')
  const violations: Violation[] = []

  for (const [pattern, use] of PHYSICAL) {
    // A fresh regex per scan. `matchAll` seeds its internal clone from the source
    // regex's `lastIndex`, and the negative control below calls `.test()` on these
    // same module-level objects — which leaves `lastIndex` non-zero and would make
    // every subsequent scan silently skip the first ~100 characters of every file.
    // Latent only because vitest happens to run `it`s in declaration order today.
    for (const match of source.matchAll(new RegExp(pattern.source, pattern.flags))) {
      // The patterns consume a leading `[;{\s]`, which is usually the newline before
      // the declaration — so measure from the property name, not the match start,
      // or a declaration at column 0 reports the previous line.
      const offset = (match.index ?? 0) + (/^[;{\s]/.test(match[0]) ? 1 : 0)
      const line = source.slice(0, offset).split('\n').length
      violations.push({
        file: relative(join(SRC, '..', '..'), file),
        line,
        text: (lines[line - 1] ?? '').trim(),
        use,
      })
    }
  }

  for (const match of source.matchAll(new RegExp(DECLARATION.source, DECLARATION.flags))) {
    const [, name, value] = match
    if (name === undefined || value === undefined) continue
    const property = name.toLowerCase()
    const count = valueTokens(value).length
    const use =
      (property === 'padding' || property === 'margin') && count === 4
        ? `${property}-block + ${property}-inline (a 4-value shorthand names left and right)`
        : property === 'border-radius' && count >= 2
          ? 'the logical corner radii (border-start-start-radius etc.)'
          : null
    if (!use) continue
    const line = source.slice(0, match.index ?? 0).split('\n').length
    violations.push({ file: relative(join(SRC, '..', '..'), file), line, text: (lines[line - 1] ?? '').trim(), use })
  }

  return violations.sort((a, b) => a.line - b.line)
}

describe('T-06 · CSS uses logical direction properties only', () => {
  const files = cssFiles(SRC)

  it('finds stylesheets to check at all', () => {
    // Without this, deleting or moving the stylesheets would make the rule below
    // vacuously true — a green check over an empty set is the FAIL-014 shape.
    expect(files.length).toBeGreaterThan(0)
  })

  it.each(files.map((f) => [relative(SRC, f), f] as const))(
    '%s declares no physical direction property',
    (_name, file) => {
      const violations = scan(file)
      expect(
        violations,
        violations.length === 0
          ? ''
          : `physical direction properties found — RTL (M3) is deferred, not abandoned:\n` +
              violations.map((v) => `  ${v.file}:${v.line}  ${v.text}  → use ${v.use}`).join('\n'),
      ).toEqual([])
    },
  )

  it('NEGATIVE CONTROL: the scanner catches a physical property it is shown', () => {
    // Proves the regexes fire before their silence is trusted. Every assertion above
    // is worthless if this one cannot go red.
    const sample = stripComments(`
      /* margin-left: 4px; — a comment, must NOT be flagged */
      .a { margin-left: 4px; text-align: right; inset-inline-start: 0; }
    `)
    // A fresh regex per probe: `.test()` on the shared objects advances `lastIndex`
    // and would corrupt every scan that runs after this one.
    const hits = PHYSICAL.filter(([pattern]) =>
      new RegExp(pattern.source, pattern.flags).test(sample),
    ).map(([, use]) => use)

    expect(hits).toContain('margin-inline-start')
    expect(hits).toContain('text-align: start / end')
    // The logical property must not be mistaken for its physical counterpart.
    expect(hits).not.toContain('inset-inline-start')

    // And the shorthand counter fires on 4 values while leaving 1–3 alone, so
    // `margin: 0 auto` does not become collateral damage.
    const shorthand = (css: string): number =>
      [...css.matchAll(new RegExp(DECLARATION.source, DECLARATION.flags))].filter(
        (m) => ['padding', 'margin'].includes((m[1] ?? '').toLowerCase()) && valueTokens(m[2] ?? '').length === 4,
      ).length
    expect(shorthand('.a { padding: 6px 8px 6px 14px; }')).toBe(1)
    expect(shorthand('.a { margin: 0 auto; padding: 0 0 4px; padding: 10px; }')).toBe(0)
    // Functions are one token, not many — `rgb(0 0 0 / 10%)` must not read as four.
    expect(valueTokens('rgb(0 0 0 / 10%)')).toHaveLength(1)
  })
})
