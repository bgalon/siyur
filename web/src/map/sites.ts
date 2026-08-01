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
 */

import maplibregl, { type Map as MapLibreMap, type Marker } from 'maplibre-gl'

import { AreaReuseSurface, type AreaReuseOptions } from './areas'
import { renderSourcedValue } from './attribution-chip'
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
 * The marker's DOM: a pin plus the display name with its attribution chip.
 * Sites with no stamped name still get a pin — the name row is simply absent
 * rather than filled in with a placeholder.
 */
export function buildMarkerElement(site: SiteRecordV1): HTMLElement {
  const element = document.createElement('div')
  element.className = 'siyur-marker'
  element.dataset.siteId = site.id

  const pin = document.createElement('span')
  pin.className = 'siyur-marker__pin'
  pin.setAttribute('aria-hidden', 'true')
  element.append(pin)

  const name = renderSourcedValue(displayName(site), { className: 'siyur-marker__name' })
  if (name) {
    element.append(name)
    element.setAttribute('aria-label', name.textContent ?? '')
  }

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

/** Place one MapLibre marker for a sanitised site record. */
export function createSiteMarker(site: SiteRecordV1, map: MapLibreMap): Marker {
  const [lon, lat] = site.location.value.coordinates
  return new maplibregl.Marker({ element: buildMarkerElement(site) })
    .setLngLat([lon, lat])
    .setPopup(new maplibregl.Popup({ closeButton: true }).setDOMContent(buildPopupContent(site)))
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

  private render(response: SitesResponse): void {
    this.clearMarkers()
    for (const site of response.sites) {
      this.markers.push(createSiteMarker(site, this.map))
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
