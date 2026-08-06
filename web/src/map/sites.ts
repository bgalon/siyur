/**
 * T042 — fetch the cited commons for the current viewport and render a marker
 * per site (FR-004 / FR-008 / US1 / US3).
 *
 * Contract: `specs/001-research-cited-sites/contracts/sites.md`
 * (`GET /sites?bbox=minLon,minLat,maxLon,maxLat`). The endpoint is built by a
 * sibling task; this module is written against the contract, which is the
 * interface.
 *
 * Two invariants drive the whole module:
 *
 * 1. **Nothing unstamped renders.** Every response passes through
 *    `sanitiseSitesResponse` and every value reaches the DOM only via
 *    `renderSourcedValue`, which returns `null` without a `source` (FR-003).
 * 2. **The client invents no text.** Names come from `names`, credits come from
 *    `attribution[]`, chip text comes from each value's own stamp.
 *
 * ## Marker density and FR-004 (read before changing `buildMarkerElement`)
 *
 * The first cut rendered every site's display name **and** its attribution chip
 * permanently. Over the Rhodes old town `GET /sites` returns ~780 records, and
 * ~780 name+chip labels inside a 700 px square overlap into unreadable noise
 * (verified in Chrome). The fix is presentational: the marker is a **dot** — the
 * ux-handoff "Research & collect" screen draws exactly this, small ink circles
 * on the map with the facts and their per-field stamps in the place-record sheet
 * — and the name arrives with its chip on hover/focus, or in the popup on click.
 *
 * **Why FR-004 still holds** ("each carrying a visible source + license
 * attribution chip"):
 *
 * - A bare dot displays no *nameable* value. What it does display — the place's
 *   position — is `site.location`, whose own chip has always lived in the popup
 *   (`LOCATION` row), never on the marker. Moving the name to the same peek
 *   surface puts it on the footing the location value already had; it does not
 *   invent a new exemption.
 * - There is still **no code path** from a value's text to the DOM that skips
 *   its chip: `renderSourcedValue` emits text and chip as one element or emits
 *   nothing. Whenever a value is shown, its stamp is shown with it, in the same
 *   element, in the same frame.
 * - The ODbL / `attribution[]` control is untouched and stays visible at all
 *   times, so the licence credit covering everything on screen never depends on
 *   an interaction (FR-004 second sentence, Constitution Article V).
 * - The dot's accessible name is `"<name> <chip text>"`, so assistive tech reads
 *   the value and its attribution together with no interaction at all. A site
 *   with no stamped name has no name we may display, so its accessible name
 *   describes the control and its stamped location instead — see
 *   {@link markerAccessibleName}. Either way the marker is focusable and named,
 *   and either way the stamp travels with whatever value the name renders.
 * - Literal compliance is preserved wherever it is physically achievable: at or
 *   below {@link SITE_LABEL_DENSITY_LIMIT} sites every marker keeps its
 *   permanent name+chip label, exactly as before. Above it, illegibly
 *   overlapping chips would *defeat* "visible attribution" rather than satisfy
 *   it — a requirement to make provenance visible is not met by rendering it
 *   unreadable.
 *
 * What would violate FR-004 is showing a name without its chip. This module
 * cannot do that: the label element *is* `renderSourcedValue`'s output.
 */

import maplibregl, { type Map as MapLibreMap, type Marker } from 'maplibre-gl'

import { AreaReuseSurface, type AreaReuseOptions } from './areas'
import { chipTextFromSource, renderSourcedValue } from './attribution-chip'
import type { OdblAttributionControl } from './attribution'
import { sanitiseSitesResponse } from './guards'
import type { GeoPoint, SiteRecordV1, SitesResponse, SourcedValue } from './types'

/** Default same-origin endpoint; override for a split API host. */
export const DEFAULT_SITES_ENDPOINT = '/sites'

/** Thrown when `GET /sites` answers with a non-2xx status (`401`, `422`, …). */
export class SitesRequestError extends Error {
  constructor(readonly status: number, message?: string) {
    super(message ?? `GET /sites failed with status ${status}`)
    this.name = 'SitesRequestError'
  }
}

/* ------------------------------------------------------------------ bbox --- */

/** The slice of `LngLatBounds` this module needs — kept structural for tests. */
export interface BboxSource {
  getWest(): number
  getSouth(): number
  getEast(): number
  getNorth(): number
}

const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n))

/**
 * `minLon,minLat,maxLon,maxLat` for the current viewport, clamped to valid
 * EPSG:4326 ranges (a world-wrapped MapLibre viewport can report |lon| > 180).
 */
export function formatBbox(bounds: BboxSource, precision = 6): string {
  const round = (n: number): string => Number(n.toFixed(precision)).toString()
  return [
    round(clamp(bounds.getWest(), -180, 180)),
    round(clamp(bounds.getSouth(), -90, 90)),
    round(clamp(bounds.getEast(), -180, 180)),
    round(clamp(bounds.getNorth(), -90, 90)),
  ].join(',')
}

/* ------------------------------------------------------------------ fetch -- */

export interface FetchSitesOptions {
  readonly bbox: string
  readonly endpoint?: string
  /** Injectable for tests; defaults to the global `fetch`. */
  readonly fetchImpl?: typeof fetch
  /** Bearer token — the commons is world-readable to any *signed-in* user. */
  readonly token?: string | null
  readonly signal?: AbortSignal
}

/**
 * Fetch and sanitise the cited sites in `bbox`.
 *
 * The returned response is already scrubbed: sites without a stamped location
 * are dropped and unstamped field values are removed, so a caller cannot render
 * an unstamped value even by accident.
 */
export async function fetchSites(options: FetchSitesOptions): Promise<SitesResponse> {
  const {
    bbox,
    endpoint = DEFAULT_SITES_ENDPOINT,
    fetchImpl = globalThis.fetch,
    token,
    signal,
  } = options

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const url = `${endpoint}?bbox=${encodeURIComponent(bbox)}`
  const response = await fetchImpl(url, { headers, ...(signal ? { signal } : {}) })
  if (!response.ok) throw new SitesRequestError(response.status)

  return sanitiseSitesResponse(await response.json())
}

/* ------------------------------------------------------- display names ----- */

const primarySubtag = (tag: string): string => (tag.split('-')[0] ?? '').toLowerCase()
const isLatnTag = (tag: string): boolean =>
  tag.split('-').some((subtag) => subtag.toLowerCase() === 'latn')

/**
 * BCP-47 keys of `names` in display-preference order:
 * **`en` → `<lang>-Latn` → source-script** (FR-008 / US3).
 *
 * An English-first user therefore always gets a readable name when one exists,
 * while the original-script value stays on the record (and in the popup).
 * Ties inside a tier are sorted so the same record always renders the same name.
 */
export function displayNameOrder(names: Readonly<Record<string, unknown>>): string[] {
  const english: string[] = []
  const latin: string[] = []
  const sourceScript: string[] = []

  for (const tag of Object.keys(names)) {
    if (primarySubtag(tag) === 'en') english.push(tag)
    else if (isLatnTag(tag)) latin.push(tag)
    else sourceScript.push(tag)
  }

  // Bare `en` outranks `en-GB` &c.; otherwise alphabetical for determinism.
  const rank = (a: string, b: string): number =>
    a.length - b.length || a.localeCompare(b)
  return [english.sort(rank), latin.sort(rank), sourceScript.sort(rank)].flat()
}

/**
 * The `SourcedValue` to show as the site's display name, or `null` when the
 * record carries no stamped name. Returns the whole sourced value — never a
 * bare string — so the chip is built from *that name's* own stamp.
 */
export function displayName(site: SiteRecordV1): SourcedValue<string> | null {
  for (const tag of displayNameOrder(site.names)) {
    const value = site.names[tag]
    if (value) return value
  }
  return null
}

/** The BCP-47 tag chosen by {@link displayName} (`null` if there is none). */
export function displayNameTag(site: SiteRecordV1): string | null {
  return displayNameOrder(site.names).find((tag) => site.names[tag] !== undefined) ?? null
}

/* ------------------------------------------------------------- rendering --- */

const formatPoint = (point: GeoPoint): string => {
  const [lon, lat] = point.coordinates
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`
}

/**
 * Sites-in-viewport count at or below which every marker keeps a permanently
 * visible name+chip label — the sparse case the ux-handoff mock draws (a
 * handful of named places on the research map).
 *
 * A deterministic, place-neutral rule: it counts what the response actually
 * returned rather than guessing at a zoom level or ranking places by an invented
 * notion of importance — no place ever loses its label for being the "wrong"
 * kind of place.
 *
 * The value is low because a full label is wide: name + a chip like
 * `OSM · ODbL-1.0 · © OPENSTREETMAP CONTRIBUTORS` runs ~330 px. Measured in
 * Chrome, a dozen labels collide as soon as their places sit within ~100 px of
 * each other. So this threshold **reduces** collisions in sparse viewports; it
 * does not guarantee none. Hover/focus/click is the reliable path to a name and
 * its attribution at any density — which is why it exists at every density,
 * including this one.
 */
export const SITE_LABEL_DENSITY_LIMIT = 12

/** How a marker presents its display name. */
export interface MarkerRenderOptions {
  /**
   * `true` — the name and its chip are rendered permanently (sparse viewport).
   * `false` (default) — the marker is a dot and the label appears on
   * hover/focus. Either way the label is `renderSourcedValue`'s output, so the
   * chip is never separable from the name.
   */
  readonly labelled?: boolean
}

/**
 * The name label for a site: `renderSourcedValue`'s `text + chip` element, or
 * `null` when the record carries no stamped name (FR-003 — nothing to show).
 */
export function buildMarkerLabel(site: SiteRecordV1): HTMLElement | null {
  return renderSourcedValue(displayName(site), { className: 'siyur-marker__name' })
}

/**
 * How a marker with no stamped name introduces itself to assistive technology.
 *
 * This is a statement about the **record** ("we hold no name for this place"),
 * not a claim about the world, so it asserts nothing that would need a source.
 */
const UNNAMED_PLACE = 'Unnamed place at'

/**
 * The marker's accessible name — never empty, never invented.
 *
 * A marker is a focusable `role="button"` that opens the popup, so WCAG 2.2
 * SC 4.1.2 (Name, Role, Value) requires it to have an accessible name. Two cases:
 *
 * - **Named site** — the label element's own text, i.e. `"<name><chip text>"`.
 *   Value and stamp arrive together with no interaction (ADR-0019, rule 5).
 * - **No stamped name** — there is no name we are permitted to display and the
 *   client invents none. The name is instead built from what the record actually
 *   carries: a description of the *control* plus `site.location`, which is
 *   guaranteed present and stamped (`sanitiseSite` drops any record without a
 *   stamped location; `SiteRecordV1.location` is non-optional server-side).
 *   Those coordinates **are** a displayed value, so the location's own chip text
 *   is emitted with them, in the same accessible name, in the same frame — the
 *   co-presence invariant, discharged for the one value being shown.
 *
 * The chip text comes from `chipTextFromSource`, the same derivation the visible
 * chip uses, so this path cannot drift into a hardcoded or guessed credit.
 */
export function markerAccessibleName(
  site: SiteRecordV1,
  label: HTMLElement | null,
): string {
  if (label) return label.textContent ?? ''
  const where = formatPoint(site.location.value)
  return `${UNNAMED_PLACE} ${where} ${chipTextFromSource(site.location.source)}`
}

/**
 * The marker's DOM: a dot, plus the display name with its attribution chip —
 * permanently when `labelled`, otherwise revealed on hover/focus (see the module
 * header for why that satisfies FR-004).
 *
 * Sites with no stamped name are a plain dot with no label and no peek: there is
 * no name to attribute, and none is invented. They remain focusable, and carry
 * an accessible name built from their stamped location — see
 * {@link markerAccessibleName}.
 */
export function buildMarkerElement(
  site: SiteRecordV1,
  options: MarkerRenderOptions = {},
): HTMLElement {
  const element = document.createElement('div')
  element.className = 'siyur-marker'
  element.dataset.siteId = site.id
  // Focusable in both modes: the popup is the full cited fact list, and reaching
  // it must never require a pointer.
  element.tabIndex = 0
  element.setAttribute('role', 'button')

  const pin = document.createElement('span')
  pin.className = 'siyur-marker__pin'
  pin.setAttribute('aria-hidden', 'true')
  element.append(pin)

  const label = buildMarkerLabel(site)
  element.dataset.labelled = String(Boolean(label && options.labelled))

  // Set BEFORE any early return. The element is already focusable with
  // `role="button"`, and a focusable button that reaches the DOM without an
  // accessible name is a WCAG 2.2 SC 4.1.2 defect — a screen-reader user hears
  // only "button". Named or not, the marker leaves here with a name, and that
  // name always carries the stamp of whatever value it renders.
  element.setAttribute('aria-label', markerAccessibleName(site, label))

  if (!label) return element

  if (options.labelled) {
    element.append(label)
    return element
  }

  // Dot mode: the label peeks on hover and on focus, so it is not mouse-only.
  const show = (): void => {
    if (!label.isConnected) element.append(label)
  }
  const hide = (): void => {
    label.remove()
  }
  element.addEventListener('pointerenter', show)
  element.addEventListener('focus', show)
  element.addEventListener('pointerleave', hide)
  element.addEventListener('blur', hide)

  return element
}

/**
 * The popup body: every *stamped* value on the record, each with its own chip —
 * including the original-script name(s), which are preserved, never overwritten
 * by the Latin display form (FR-008).
 */
export function buildPopupContent(site: SiteRecordV1): HTMLElement {
  const root = document.createElement('div')
  root.className = 'siyur-popup'
  root.dataset.siteId = site.id

  const rows: (HTMLElement | null)[] = []
  const chosen = displayNameTag(site)

  for (const tag of displayNameOrder(site.names)) {
    rows.push(
      renderSourcedValue(site.names[tag], {
        label: tag.toUpperCase(),
        className: tag === chosen ? 'siyur-popup__name' : 'siyur-popup__alt-name',
      }),
    )
  }

  rows.push(
    renderSourcedValue<GeoPoint>(site.location, {
      label: 'LOCATION',
      format: formatPoint,
    }),
  )
  for (const category of site.categories ?? []) {
    rows.push(renderSourcedValue(category, { label: 'CATEGORY' }))
  }
  rows.push(renderSourcedValue(site.address, { label: 'ADDRESS' }))
  rows.push(renderSourcedValue(site.opening_hours, { label: 'HOURS' }))

  for (const row of rows) {
    if (!row) continue // unstamped ⇒ not rendered (FR-003)
    const line = document.createElement('div')
    line.className = 'siyur-popup__row'
    line.append(row)
    root.append(line)
  }

  return root
}

/**
 * Place one MapLibre marker for a sanitised site record.
 *
 * The popup body is built on **first open**, not up front: a dense viewport used
 * to construct ~780 full fact-lists — every name, location, category, address
 * and hours row with its chip, ~20 000 DOM nodes measured — of which the user
 * opens perhaps one. Same DOM, same content, produced when it is needed.
 */
export function createSiteMarker(
  site: SiteRecordV1,
  map: MapLibreMap,
  options: MarkerRenderOptions = {},
): Marker {
  const [lon, lat] = site.location.value.coordinates
  const popup = new maplibregl.Popup({ closeButton: true })
  let built = false
  popup.on('open', () => {
    if (built) return
    built = true
    popup.setDOMContent(buildPopupContent(site))
  })

  return new maplibregl.Marker({ element: buildMarkerElement(site, options) })
    .setLngLat([lon, lat])
    .setPopup(popup)
    .addTo(map)
}

/* ----------------------------------------------------------------- layer --- */

export interface SitesLayerOptions {
  readonly endpoint?: string
  readonly fetchImpl?: typeof fetch
  /**
   * Supplies the bearer token. Auth plumbing (Google SSO → JWT) belongs to the
   * api/auth task; until it lands this simply yields `null` and the request goes
   * out unauthenticated, which the endpoint answers with `401`.
   */
  readonly getToken?: () => string | null | undefined | Promise<string | null | undefined>
  readonly onError?: (error: unknown) => void
  readonly onSites?: (response: SitesResponse) => void
  /**
   * Override {@link SITE_LABEL_DENSITY_LIMIT} — the site count at or below which
   * every marker keeps a permanent name+chip label.
   */
  readonly labelDensityLimit?: number
}

/**
 * Keeps the map's markers and attribution in sync with the viewport.
 *
 * One in-flight request at a time: a new `refresh()` aborts the previous one, so
 * a fast pan cannot land stale markers on top of fresh ones.
 */
export class SitesLayer {
  private markers: Marker[] = []
  private inFlight: AbortController | null = null
  private labelled = true
  private readonly onMoveEnd = (): void => {
    void this.refresh()
  }

  constructor(
    private readonly map: MapLibreMap,
    private readonly attribution: OdblAttributionControl | null = null,
    private readonly options: SitesLayerOptions = {},
  ) {}

  /** Refresh on every viewport change, and once immediately. */
  start(): this {
    this.map.on('moveend', this.onMoveEnd)
    void this.refresh()
    return this
  }

  /** Fetch the current viewport and re-render. Resolves `null` if superseded. */
  async refresh(): Promise<SitesResponse | null> {
    this.inFlight?.abort()
    const controller = new AbortController()
    this.inFlight = controller

    try {
      const token = (await this.options.getToken?.()) ?? null
      const response = await fetchSites({
        bbox: formatBbox(this.map.getBounds()),
        ...(this.options.endpoint ? { endpoint: this.options.endpoint } : {}),
        ...(this.options.fetchImpl ? { fetchImpl: this.options.fetchImpl } : {}),
        token,
        signal: controller.signal,
      })
      if (controller.signal.aborted) return null

      this.render(response)
      this.options.onSites?.(response)
      return response
    } catch (error) {
      if (controller.signal.aborted) return null
      this.options.onError?.(error)
      return null
    } finally {
      if (this.inFlight === controller) this.inFlight = null
    }
  }

  /** Markers currently on the map (test/debug seam). */
  get markerCount(): number {
    return this.markers.length
  }

  /**
   * Whether the last render was sparse enough for permanent name+chip labels.
   * `false` means the markers are dots and the labels peek on hover/focus.
   */
  get labelsVisible(): boolean {
    return this.labelled
  }

  private render(response: SitesResponse): void {
    this.clearMarkers()
    const limit = this.options.labelDensityLimit ?? SITE_LABEL_DENSITY_LIMIT
    // One decision per response, applied uniformly: no per-marker ranking, so
    // which places keep a label never depends on the place itself.
    this.labelled = response.sites.length <= limit
    for (const site of response.sites) {
      this.markers.push(createSiteMarker(site, this.map, { labelled: this.labelled }))
    }
    // T044: the credit line is driven by the response, not by our own guesswork.
    this.attribution?.setResponseAttributions(response.attribution)
  }

  private clearMarkers(): void {
    for (const marker of this.markers) marker.remove()
    this.markers = []
  }

  destroy(): void {
    this.inFlight?.abort()
    this.inFlight = null
    this.map.off('moveend', this.onMoveEnd)
    this.clearMarkers()
  }
}

/** Create and start a {@link SitesLayer}. */
export function mountSitesLayer(
  map: MapLibreMap,
  attribution: OdblAttributionControl | null = null,
  options: SitesLayerOptions = {},
): SitesLayer {
  return new SitesLayer(map, attribution, options).start()
}

/* -------------------------------------------------------- reuse (T052) ----- */

/**
 * T052 — bind a {@link SitesLayer} to the US2 reuse surface (`./areas`).
 *
 * "Show the existing cited sites" for a covered area is exactly this layer's
 * `refresh()`: one `GET /sites` over the viewport, which the contract defines as
 * read-only ("no research triggered"). Wiring it here means the covered path can
 * only ever *read* the commons — {@link AreaReuseSurface} holds no research code
 * of its own, so a covered area cannot be auto-researched (FR-006).
 */
export function mountAreaReuse(
  layer: SitesLayer,
  container: HTMLElement,
  options: Omit<AreaReuseOptions, 'showExistingSites'> = {},
): AreaReuseSurface {
  return new AreaReuseSurface(container, {
    ...options,
    showExistingSites: async () => {
      await layer.refresh()
    },
  })
}
