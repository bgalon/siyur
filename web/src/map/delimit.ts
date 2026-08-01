/**
 * The area-delimit control — the missing first step of US1.
 *
 * The US1 acceptance criterion begins "sign in → **delimit the area** → trigger
 * research". T042–T045 built the marker layer, T052 built the reuse surface, but
 * nothing ever built the affordance that *produces* an area. This module is it:
 * the user's gesture that turns the map into an `AreaRequest` for `POST /areas`
 * (`specs/001-research-cited-sites/contracts/areas.md`).
 *
 * Design authority: `docs/design/ux-handoff/README.md` § Screens 1 ("Define the
 * area") — a full-bleed map with a floating search pill and map controls, over a
 * bottom sheet holding the commons-coverage card and the CTA. Two affordances
 * live here:
 *
 * - the **search pill** → `{ name }`, resolved server-side (Overture divisions +
 *   Nominatim disambiguation, per the contract);
 * - the **map-extent button** (the mock's ✎ slot) → `{ bbox }` taken from the
 *   *current viewport*.
 *
 * The viewport route stands in for the mock's freehand draw tool: it needs no
 * drawing code, `contracts/areas.md` accepts a `bbox` directly, and it is exactly
 * as generic — the numbers come from wherever the user has panned the map. A
 * drag-to-draw rectangle would be a strictly richer geometry over the same
 * request field, and can replace this without touching anything downstream.
 *
 * **Genericity (FR-001 / SC-005) is structural here.** This module contains no
 * place name, no coordinate and no default extent. Every value it sends is read
 * from the user's input box or from the map's own bounds at click time. There is
 * nothing to hardcode a place *into*.
 */

import type { AreaRequest } from './areas'
import type { BboxSource } from './sites'

/** `[minLon, minLat, maxLon, maxLat]`, EPSG:4326 — the `POST /areas` bbox form. */
export type Bbox = [number, number, number, number]

const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n))
const round = (n: number, precision: number): number => Number(n.toFixed(precision))

/**
 * The current viewport as an EPSG:4326 bbox, clamped to valid ranges (a
 * world-wrapped MapLibre viewport can report |lon| > 180).
 *
 * Mirrors `sites.formatBbox`, which produces the same numbers as the query-string
 * form for `GET /sites`; this one stays numeric because `POST /areas` takes a
 * JSON array.
 */
export function viewportBbox(bounds: BboxSource, precision = 6): Bbox {
  return [
    round(clamp(bounds.getWest(), -180, 180), precision),
    round(clamp(bounds.getSouth(), -90, 90), precision),
    round(clamp(bounds.getEast(), -180, 180), precision),
    round(clamp(bounds.getNorth(), -90, 90), precision),
  ]
}

/**
 * A bbox is usable only if it has area. A degenerate extent (zero width or
 * height) would be answered `422` by the contract, so the control refuses it
 * locally rather than sending a request it knows is invalid.
 */
export function isUsableBbox(bbox: Bbox): boolean {
  const [minLon, minLat, maxLon, maxLat] = bbox
  return bbox.every(Number.isFinite) && maxLon > minLon && maxLat > minLat
}

/* ------------------------------------------------------------- control --- */

export interface DelimitControlOptions {
  /** The live map bounds. Read at click time — never captured at mount. */
  readonly getBounds: () => BboxSource
  /** Called with the user's delimited area; the caller does `POST /areas`. */
  readonly onDelimit: (area: AreaRequest) => void | Promise<void>
  readonly onError?: (error: unknown) => void
}

/** Text the control renders. No place name appears in any of it. */
const COPY = {
  searchLabel: 'Name an area to research',
  searchPlaceholder: 'Name an area…',
  submit: 'Find',
  viewport: 'Use this view',
  viewportTitle: 'Delimit the area currently shown on the map',
  degenerate: 'Zoom or pan the map first — this view has no extent to research.',
} as const

/**
 * The floating "define the area" pill (ux-handoff Screen 1).
 *
 * Nothing here resolves an area or starts research: it only builds the
 * `AreaRequest` and hands it to `onDelimit`. `POST /areas` is the caller's, and
 * the covered-vs-not decision stays where it belongs, in `areas.ts`.
 */
export class DelimitControl {
  private readonly root: HTMLElement
  private readonly form: HTMLFormElement
  private readonly input: HTMLInputElement
  private readonly submit: HTMLButtonElement
  private readonly viewport: HTMLButtonElement
  private readonly hint: HTMLParagraphElement

  constructor(
    private readonly container: HTMLElement,
    private readonly options: DelimitControlOptions,
  ) {
    this.root = document.createElement('section')
    this.root.className = 'siyur-delimit'
    this.root.dataset.busy = 'false'

    this.form = document.createElement('form')
    this.form.className = 'siyur-delimit__search'
    this.form.setAttribute('role', 'search')

    const glyph = document.createElement('span')
    glyph.className = 'siyur-delimit__glyph'
    glyph.setAttribute('aria-hidden', 'true')
    glyph.textContent = '⌕'

    this.input = document.createElement('input')
    this.input.className = 'siyur-delimit__input'
    this.input.type = 'search'
    this.input.name = 'area'
    this.input.autocomplete = 'off'
    this.input.placeholder = COPY.searchPlaceholder
    this.input.setAttribute('aria-label', COPY.searchLabel)

    this.submit = document.createElement('button')
    this.submit.className = 'siyur-delimit__submit'
    this.submit.type = 'submit'
    this.submit.textContent = COPY.submit

    this.form.append(glyph, this.input, this.submit)

    this.viewport = document.createElement('button')
    this.viewport.className = 'siyur-delimit__viewport'
    this.viewport.type = 'button'
    this.viewport.dataset.action = 'use-viewport'
    this.viewport.title = COPY.viewportTitle
    this.viewport.textContent = COPY.viewport

    this.hint = document.createElement('p')
    this.hint.className = 'siyur-delimit__hint'
    this.hint.hidden = true

    this.root.append(this.form, this.viewport, this.hint)
    this.container.append(this.root)

    this.form.addEventListener('submit', this.onSubmit)
    this.viewport.addEventListener('click', this.onViewportClick)
  }

  /** The mounted element (test/debug seam). */
  get element(): HTMLElement {
    return this.root
  }

  private readonly onSubmit = (event: Event): void => {
    event.preventDefault()
    const name = this.input.value.trim()
    // An empty box is not a delimited area; ask again rather than send `{}`.
    if (!name) {
      this.input.focus()
      return
    }
    void this.emit({ name })
  }

  private readonly onViewportClick = (): void => {
    let bbox: Bbox
    try {
      bbox = viewportBbox(this.options.getBounds())
    } catch (error) {
      this.options.onError?.(error)
      return
    }
    if (!isUsableBbox(bbox)) {
      this.showHint(COPY.degenerate)
      return
    }
    void this.emit({ bbox })
  }

  /** Hand the delimited area to the caller, guarding against a double-submit. */
  private async emit(area: AreaRequest): Promise<void> {
    if (this.root.dataset.busy === 'true') return
    this.setBusy(true)
    this.showHint(null)
    try {
      await this.options.onDelimit(area)
    } catch (error) {
      this.options.onError?.(error)
    } finally {
      this.setBusy(false)
    }
  }

  private setBusy(busy: boolean): void {
    this.root.dataset.busy = String(busy)
    this.submit.disabled = busy
    this.viewport.disabled = busy
  }

  private showHint(text: string | null): void {
    this.hint.textContent = text ?? ''
    this.hint.hidden = text === null
  }

  destroy(): void {
    this.form.removeEventListener('submit', this.onSubmit)
    this.viewport.removeEventListener('click', this.onViewportClick)
    this.root.remove()
  }
}

/** Create and mount a {@link DelimitControl}. */
export function mountDelimitControl(
  container: HTMLElement,
  options: DelimitControlOptions,
): DelimitControl {
  return new DelimitControl(container, options)
}
