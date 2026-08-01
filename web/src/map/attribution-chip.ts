/**
 * T043 — per-value source + license attribution chip (FR-004 / SC-002).
 *
 * **The chip text comes ONLY from the value's own `source` stamp.** There is no
 * table of licenses, no `kind → publisher` map, no default string: the client
 * renders `kind`, `license` and (when the license requires credit) the exact
 * `attribution` the server stamped, and nothing else. If the server ever stops
 * sending a field, the chip loses that field — it does not gain a guess.
 *
 * {@link renderSourcedValue} is the ONLY way a value reaches the DOM in this
 * app. It returns `null` for an unstamped value, so "a value without a source is
 * never rendered" is a structural property, not a review discipline.
 *
 * Visual encoding follows `docs/design/ux-handoff/README.md` § Provenance stamps:
 * mono uppercase micro-chip; solid border when `bundleable`, dashed when the
 * value cannot enter an offline bundle.
 */

import { isSourceRef } from './guards'
import type { SourceRef, SourcedValue } from './types'

/**
 * The chip's text, derived from the stamp alone.
 *
 * `KIND · LICENSE` — plus the stamped attribution string when the source
 * carries one. Never localised, never abbreviated against a lookup table.
 */
export function chipTextFromSource(source: SourceRef): string {
  const parts = [source.kind.trim().toUpperCase(), source.license.trim()]
  const attribution = source.attribution?.trim()
  if (attribution) parts.push(attribution)
  return parts.join(' · ')
}

/**
 * Build the chip element for a stamped value.
 *
 * @returns the chip, or `null` if `value` carries no usable `source` stamp —
 * callers must treat `null` as "this value may not be displayed".
 */
export function createAttributionChip(value: unknown): HTMLElement | null {
  const source: unknown = (value as { source?: unknown } | null | undefined)?.source
  if (!isSourceRef(source)) return null

  const chip = document.createElement('span')
  chip.className = 'siyur-chip'
  chip.dataset.sourceKind = source.kind
  chip.dataset.license = source.license
  // Dashed border = leaves the bundle (ux-handoff encoding, systemwide).
  const bundleable = (value as { bundleable?: unknown }).bundleable
  chip.dataset.bundleable = bundleable === false ? 'false' : 'true'
  chip.textContent = chipTextFromSource(source)
  chip.title = chip.textContent
  return chip
}

/** Options for {@link renderSourcedValue}. */
export interface RenderSourcedValueOptions<T> {
  /** Field name shown before the value (e.g. `HOURS`). Omitted when absent. */
  readonly label?: string
  /** Turn the payload into display text. Defaults to `String(value)`. */
  readonly format?: (value: T) => string
  /** Extra class on the wrapper, for per-field styling. */
  readonly className?: string
}

/**
 * Render one sourced value as `[label] text [chip]`.
 *
 * **The single funnel from data to DOM.** Returns `null` — rendering nothing at
 * all — whenever the value is unstamped. Nothing in `sites.ts` reads `.value`
 * for display except through this function.
 */
export function renderSourcedValue<T>(
  value: SourcedValue<T> | null | undefined,
  options: RenderSourcedValueOptions<T> = {},
): HTMLElement | null {
  const chip = createAttributionChip(value)
  // `chip === null` ⇒ no stamp ⇒ the value must not be displayed (FR-003).
  if (!chip || !value) return null

  const wrapper = document.createElement('span')
  wrapper.className = options.className
    ? `siyur-value ${options.className}`
    : 'siyur-value'

  if (options.label) {
    const label = document.createElement('span')
    label.className = 'siyur-value__label'
    label.textContent = options.label
    wrapper.append(label)
  }

  const text = document.createElement('span')
  text.className = 'siyur-value__text'
  text.textContent = (options.format ?? String)(value.value)
  wrapper.append(text, chip)

  return wrapper
}
