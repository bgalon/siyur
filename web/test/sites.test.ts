import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { Map as MapLibreMap } from 'maplibre-gl'

// jsdom has no WebGL, so MapLibre's Marker/Popup are stubbed. The backend
// `GET /sites` does not exist yet either — the CONTRACT is the interface, so the
// fetch is mocked with the contract's own worked example
// (specs/001-research-cited-sites/contracts/sites.md).
interface PopupLike {
  content: HTMLElement | null
  builds: number
  fire(type: string): void
}

const stub = vi.hoisted(() => ({
  markers: [] as {
    opts: { element?: HTMLElement }
    lngLat: [number, number] | null
    popup: { content: HTMLElement | null; builds: number; fire(type: string): void } | null
    added: boolean
    removed: boolean
  }[],
}))

vi.mock('maplibre-gl', () => {
  class FakePopup {
    content: HTMLElement | null = null
    /** How many times the content was actually constructed (laziness probe). */
    builds = 0
    private listeners: Record<string, (() => void)[]> = {}
    constructor(public opts: Record<string, unknown>) {}
    on(type: string, listener: () => void) {
      ;(this.listeners[type] ??= []).push(listener)
      return this
    }
    /** Stand-in for MapLibre firing `open` when the popup is added to the map. */
    fire(type: string) {
      for (const listener of this.listeners[type] ?? []) listener()
      return this
    }
    setDOMContent(element: HTMLElement) {
      this.content = element
      this.builds += 1
      return this
    }
  }
  class FakeMarker {
    lngLat: [number, number] | null = null
    popup: FakePopup | null = null
    added = false
    removed = false
    constructor(public opts: { element?: HTMLElement }) {
      stub.markers.push(this as never)
    }
    setLngLat(value: [number, number]) {
      this.lngLat = value
      return this
    }
    setPopup(popup: FakePopup) {
      this.popup = popup
      return this
    }
    addTo(_map: unknown) {
      this.added = true
      return this
    }
    remove() {
      this.removed = true
      return this
    }
  }
  return {
    default: { Marker: FakeMarker, Popup: FakePopup },
    Marker: FakeMarker,
    Popup: FakePopup,
  }
})

const {
  SITE_LABEL_DENSITY_LIMIT,
  SitesLayer,
  SitesRequestError,
  buildMarkerElement,
  buildPopupContent,
  createSiteMarker,
  displayName,
  displayNameOrder,
  fetchSites,
  formatBbox,
  mountAreaReuse,
} = await import('../src/map/sites')
const { OdblAttributionControl } = await import('../src/map/attribution')
const { sanitiseSite } = await import('../src/map/guards')

/* ------------------------------------------------------------- fixtures --- */

const OVERTURE = {
  kind: 'overture',
  id: '08f394…',
  license: 'CDLA-Permissive-2.0',
  attribution: null,
}
const OSM = {
  kind: 'osm',
  id: 'node/123456',
  license: 'ODbL-1.0',
  attribution: '© OpenStreetMap contributors',
}

/** The contract's worked example, verbatim. */
const PALACE = {
  id: '6f1c-uuid',
  gers_id: '08f394…gers',
  schema_ver: 'SiteRecordV1',
  names: {
    en: {
      value: 'Palace of the Grand Master',
      source: OVERTURE,
      bundleable: true,
      confidence: 0.82,
      observed_at: '2026-07-22',
    },
    el: {
      value: 'Ρολόι',
      source: OSM,
      bundleable: true,
      confidence: 0.7,
      observed_at: '2026-07-20',
    },
    'el-Latn': {
      value: 'Roloi',
      source: OSM,
      bundleable: true,
      confidence: 0.6,
      observed_at: '2026-07-31',
    },
  },
  location: {
    value: { type: 'Point', coordinates: [28.2247, 36.4443] },
    source: OVERTURE,
    bundleable: true,
    confidence: 0.9,
    observed_at: '2026-07-22',
  },
  categories: [
    {
      value: 'attraction.castle',
      source: OVERTURE,
      bundleable: true,
      confidence: 0.8,
      observed_at: '2026-07-22',
    },
  ],
  address: null,
  opening_hours: null,
  conflicts: [],
  updated_at: '2026-07-22T09:00:00Z',
}

/** Greek-only site — the `el` / `el-Latn` case from the contract (FR-008 / US3). */
const CLOCK_TOWER = {
  id: 'a20e-uuid',
  gers_id: null,
  schema_ver: 'SiteRecordV1',
  names: {
    el: { value: 'Ρολόι', source: OSM, bundleable: true, confidence: 0.7 },
    'el-Latn': { value: 'Roloi', source: OSM, bundleable: true, confidence: 0.6 },
  },
  location: {
    value: { type: 'Point', coordinates: [28.2235, 36.4451] },
    source: OSM,
    bundleable: true,
    confidence: 0.7,
  },
  categories: [],
  conflicts: [],
  updated_at: '2026-07-20T14:00:00Z',
}

const RESPONSE = {
  sites: [PALACE, CLOCK_TOWER],
  attribution: ['© OpenStreetMap contributors'],
}

/**
 * `n` distinct Rhodes-scale sites — the shape of the real problem: `GET /sites`
 * over the old town returns ~780 records into a ~700 px square.
 */
const manySites = (n: number): unknown[] =>
  Array.from({ length: n }, (_, i) => ({
    ...CLOCK_TOWER,
    id: `dense-${i}`,
    location: {
      ...CLOCK_TOWER.location,
      value: { type: 'Point', coordinates: [28.2235 + i * 0.0001, 36.4451 + i * 0.0001] },
    },
  }))

const okFetch = (body: unknown): typeof fetch =>
  vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch

const fakeMap = (): MapLibreMap & { handlers: Record<string, (() => void)[]> } => {
  const handlers: Record<string, (() => void)[]> = {}
  return {
    handlers,
    on(event: string, handler: () => void) {
      ;(handlers[event] ??= []).push(handler)
      return this
    },
    off(event: string, handler: () => void) {
      handlers[event] = (handlers[event] ?? []).filter((h) => h !== handler)
      return this
    },
    getBounds: () => ({
      getWest: () => 28.2,
      getSouth: () => 36.44,
      getEast: () => 28.24,
      getNorth: () => 36.45,
    }),
  } as unknown as MapLibreMap & { handlers: Record<string, (() => void)[]> }
}

beforeEach(() => {
  stub.markers.length = 0
})

/* ------------------------------------------------------------------ bbox --- */

describe('formatBbox', () => {
  it('emits minLon,minLat,maxLon,maxLat in contract order', () => {
    expect(
      formatBbox({
        getWest: () => 28.2,
        getSouth: () => 36.44,
        getEast: () => 28.24,
        getNorth: () => 36.45,
      }),
    ).toBe('28.2,36.44,28.24,36.45')
  })

  it('clamps a world-wrapped viewport to valid EPSG:4326 ranges', () => {
    expect(
      formatBbox({
        getWest: () => -400,
        getSouth: () => -120,
        getEast: () => 400,
        getNorth: () => 120,
      }),
    ).toBe('-180,-90,180,90')
  })
})

/* ----------------------------------------------------------------- fetch --- */

describe('fetchSites', () => {
  it('requests the viewport bbox and sends the bearer token', async () => {
    const spy = okFetch(RESPONSE)
    await fetchSites({ bbox: '28.2,36.44,28.24,36.45', token: 'tok-123', fetchImpl: spy })
    const [url, init] = (spy as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ]
    expect(url).toBe(`/sites?bbox=${encodeURIComponent('28.2,36.44,28.24,36.45')}`)
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123')
  })

  it('omits the Authorization header when there is no token yet', async () => {
    const spy = okFetch(RESPONSE)
    await fetchSites({ bbox: '0,0,1,1', fetchImpl: spy })
    const [, init] = (spy as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ]
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined()
  })

  it.each([401, 422, 500])('raises SitesRequestError on %i', async (status) => {
    const failing = vi.fn(async () => new Response('', { status })) as unknown as typeof fetch
    await expect(fetchSites({ bbox: '0,0,1,1', fetchImpl: failing })).rejects.toBeInstanceOf(
      SitesRequestError,
    )
  })

  it('passes the contract fixture through intact', async () => {
    const response = await fetchSites({ bbox: '0,0,1,1', fetchImpl: okFetch(RESPONSE) })
    expect(response.sites).toHaveLength(2)
    expect(response.attribution).toEqual(['© OpenStreetMap contributors'])
  })
})

/* ------------------------------------------------ display-name fallback --- */

describe('display name preference: en → <lang>-Latn → source-script (FR-008 / US3)', () => {
  it('orders the contract fixture’s BCP-47 keys en, el-Latn, el', () => {
    expect(displayNameOrder(PALACE.names)).toEqual(['en', 'el-Latn', 'el'])
  })

  it('prefers en when the record has one', () => {
    expect(displayName(PALACE as never)?.value).toBe('Palace of the Grand Master')
  })

  it('falls back to <lang>-Latn for a Greek-only record', () => {
    // el + el-Latn, no en → the English-first user reads "Roloi".
    expect(displayName(CLOCK_TOWER as never)?.value).toBe('Roloi')
  })

  it('falls back to the source script when no Latin form exists', () => {
    const greekOnly = { ...CLOCK_TOWER, names: { el: CLOCK_TOWER.names.el } }
    expect(displayName(greekOnly as never)?.value).toBe('Ρολόι')
  })

  it('carries the chosen name’s OWN stamp, not the record’s or the location’s', () => {
    // `el-Latn` inherits the ODbL stamp of the `el` value it was derived from.
    expect(displayName(CLOCK_TOWER as never)?.source.license).toBe('ODbL-1.0')
    expect(displayName(PALACE as never)?.source.license).toBe('CDLA-Permissive-2.0')
  })

  it('skips an unstamped preferred name rather than showing it', async () => {
    const tampered = {
      sites: [
        {
          ...CLOCK_TOWER,
          names: {
            en: { value: 'Clock Tower' }, // model-asserted: no source stamp
            'el-Latn': CLOCK_TOWER.names['el-Latn'],
          },
        },
      ],
      attribution: [],
    }
    const response = await fetchSites({ bbox: '0,0,1,1', fetchImpl: okFetch(tampered) })
    const site = response.sites[0]!
    expect(Object.keys(site.names)).toEqual(['el-Latn'])
    expect(displayName(site)?.value).toBe('Roloi')
  })

  it('returns null when the record has no stamped name at all', () => {
    expect(displayName({ ...CLOCK_TOWER, names: {} } as never)).toBeNull()
  })
})

/* ---------------------------------------------- T062 · rendered fallback --- */

/**
 * T062 pins the *rendered* consequence of the FR-008 ordering across all three
 * tiers, and the half of US3 that is easy to regress: the display form never
 * replaces the original — the source-script value stays on the record and in the
 * popup in **every** case, including the one where English wins.
 *
 * The ordering itself already existed in `sites.ts` (T042); this test fixes it.
 */
describe('T062 · display name resolves en → <lang>-Latn → source-script', () => {
  /** Names of a Greek-only fixture, tier by tier. */
  const GREEK_SCRIPT_ONLY = { el: CLOCK_TOWER.names.el }
  const GREEK_WITH_LATIN = CLOCK_TOWER.names
  const GREEK_WITH_ENGLISH = {
    ...CLOCK_TOWER.names,
    en: { value: 'Clock Tower', source: OVERTURE, bundleable: true, confidence: 0.8 },
  }

  /**
   * The name a marker actually shows. Markers are dots by default now, so this
   * peeks the label first — the same reveal a user gets on hover or focus.
   */
  const markerName = (names: unknown): string | null | undefined => {
    const element = buildMarkerElement({ ...CLOCK_TOWER, names } as never)
    element.dispatchEvent(new Event('pointerenter'))
    return element.querySelector('.siyur-value__text')?.textContent
  }

  it.each([
    ['source script (el only)', GREEK_SCRIPT_ONLY, 'Ρολόι'],
    ['el-Latn over el', GREEK_WITH_LATIN, 'Roloi'],
    ['en over both', GREEK_WITH_ENGLISH, 'Clock Tower'],
  ])('renders %s', (_label, names, expected) => {
    expect(displayName({ ...CLOCK_TOWER, names } as never)?.value).toBe(expected)
    expect(markerName(names)).toBe(expected)
  })

  it.each([
    ['source script (el only)', GREEK_SCRIPT_ONLY],
    ['el-Latn over el', GREEK_WITH_LATIN],
    ['en over both', GREEK_WITH_ENGLISH],
  ])('keeps the original script on the record and in the popup — %s', (_label, names) => {
    const site = { ...CLOCK_TOWER, names } as never
    // US3 preserves the original, it does not replace it.
    expect((names as Record<string, { value: string } | undefined>).el?.value).toBe('Ρολόι')
    expect(buildPopupContent(site).textContent).toMatch(/Ρολόι/)
  })

  it('renders the original with the ORIGINAL’s own stamp, not the display form’s', () => {
    // `en` here is Overture-stamped while `el` is OSM/ODbL: the Greek row must
    // keep its own ODbL chip even though English is what the marker shows.
    const popup = buildPopupContent({ ...CLOCK_TOWER, names: GREEK_WITH_ENGLISH } as never)
    const rows = [...popup.querySelectorAll('.siyur-popup__row')]
    const greekRow = rows.find((row) => row.textContent?.includes('Ρολόι'))
    expect(greekRow?.querySelector('.siyur-chip')?.textContent).toMatch(/ODbL-1\.0/)
    expect(displayName({ ...CLOCK_TOWER, names: GREEK_WITH_ENGLISH } as never)?.source.kind).toBe(
      'overture',
    )
  })
})

/* ------------------------------------------------------------- rendering --- */

const CLOCK_TOWER_CHIP = 'OSM · ODbL-1.0 · © OpenStreetMap contributors'

describe('marker rendering', () => {
  it('renders the display name with its own attribution chip when labelled', () => {
    const element = buildMarkerElement(CLOCK_TOWER as never, { labelled: true })
    expect(element.dataset.siteId).toBe('a20e-uuid')
    expect(element.dataset.labelled).toBe('true')
    expect(element.querySelector('.siyur-value__text')?.textContent).toBe('Roloi')
    expect(element.querySelector('.siyur-chip')?.textContent).toBe(CLOCK_TOWER_CHIP)
  })

  it('renders a pin but no name row when the record carries no stamped name', () => {
    const element = buildMarkerElement({ ...CLOCK_TOWER, names: {} } as never, { labelled: true })
    expect(element.querySelector('.siyur-marker__pin')).not.toBeNull()
    expect(element.querySelector('.siyur-value')).toBeNull()
    expect(element.querySelector('.siyur-chip')).toBeNull()
  })

  it('gives every displayed popup value its own chip and preserves the original script', () => {
    const popup = buildPopupContent(PALACE as never)
    const rows = [...popup.querySelectorAll('.siyur-popup__row')]
    expect(rows.length).toBeGreaterThan(0)
    for (const row of rows) {
      expect(row.querySelector('.siyur-chip')).not.toBeNull()
    }
    // The Greek original is still there next to the Latin display form (FR-008).
    expect(popup.textContent).toMatch(/Ρολόι/)
    expect(popup.textContent).toMatch(/Roloi/)
    expect(popup.textContent).toMatch(/attraction\.castle/)
  })

  it('renders no row for an unstamped field (FR-003 / SC-002)', () => {
    const tampered = {
      ...PALACE,
      // Both arrive without a source: a model-asserted category and a bare address.
      categories: [{ value: 'attraction.invented' }],
      address: { value: 'Οδός Ιπποτών 1' },
    }
    const popup = buildPopupContent(tampered as never)
    expect(popup.textContent).not.toMatch(/attraction\.invented/)
    expect(popup.textContent).not.toMatch(/Ιπποτών/)
    // …and nothing rendered anywhere is missing a chip.
    for (const row of popup.querySelectorAll('.siyur-popup__row')) {
      expect(row.querySelector('.siyur-chip')).not.toBeNull()
    }
  })
})

/* ------------------------------------------------- dense-viewport markers --- */

const peek = (element: HTMLElement, type: 'pointerenter' | 'focus'): void => {
  element.dispatchEvent(new Event(type))
}

describe('dot markers keep dense viewports readable without weakening FR-004', () => {
  it('renders a bare dot — no name, no chip — until the user asks', () => {
    const element = buildMarkerElement(CLOCK_TOWER as never)
    expect(element.dataset.labelled).toBe('false')
    expect(element.querySelector('.siyur-marker__pin')).not.toBeNull()
    expect(element.querySelector('.siyur-value')).toBeNull()
    expect(element.querySelector('.siyur-chip')).toBeNull()
    // A dot displays no nameable value, so no value goes unattributed.
    expect(element.textContent).toBe('')
  })

  it('reveals the name AND its chip together on hover', () => {
    const element = buildMarkerElement(CLOCK_TOWER as never)
    peek(element, 'pointerenter')
    expect(element.querySelector('.siyur-value__text')?.textContent).toBe('Roloi')
    expect(element.querySelector('.siyur-chip')?.textContent).toBe(CLOCK_TOWER_CHIP)
  })

  it('reveals the same name+chip on keyboard focus, so the peek is not mouse-only', () => {
    const element = buildMarkerElement(CLOCK_TOWER as never)
    expect(element.tabIndex).toBe(0)
    peek(element, 'focus')
    expect(element.querySelector('.siyur-chip')?.textContent).toBe(CLOCK_TOWER_CHIP)
  })

  it('hides the label again on pointerleave', () => {
    const element = buildMarkerElement(CLOCK_TOWER as never)
    peek(element, 'pointerenter')
    element.dispatchEvent(new Event('pointerleave'))
    expect(element.querySelector('.siyur-value')).toBeNull()
    expect(element.querySelector('.siyur-chip')).toBeNull()
  })

  it('never shows the name without its chip — the label IS the chip’s element', () => {
    const element = buildMarkerElement(PALACE as never)
    peek(element, 'pointerenter')
    const values = [...element.querySelectorAll('.siyur-value')]
    expect(values).toHaveLength(1)
    for (const value of values) {
      expect(value.querySelector('.siyur-chip')).not.toBeNull()
      expect(value.textContent).toMatch(/Palace of the Grand Master/)
    }
  })

  it('carries name + attribution in the dot’s accessible name, with no interaction', () => {
    const element = buildMarkerElement(CLOCK_TOWER as never)
    const label = element.getAttribute('aria-label') ?? ''
    expect(label).toMatch(/Roloi/)
    expect(label).toMatch(/ODbL-1\.0/)
    expect(label).toMatch(/OpenStreetMap contributors/)
  })

  it('peeks nothing at all for a record with no stamped name (FR-003)', () => {
    const element = buildMarkerElement({ ...CLOCK_TOWER, names: {} } as never)
    peek(element, 'pointerenter')
    expect(element.querySelector('.siyur-value')).toBeNull()
    expect(element.querySelector('.siyur-chip')).toBeNull()
    // Nothing renders visually, and no name is invented to fill the gap. The
    // element still needs an accessible name — see the SC 4.1.2 block below for
    // what it is allowed to be built from.
    const ariaLabel = element.getAttribute('aria-label') ?? ''
    expect(ariaLabel).toBeTruthy()
    expect(ariaLabel).not.toMatch(/Ρολόι|Roloi/)
  })

  it('skips an unstamped preferred name when peeking, showing the stamped one', () => {
    const tampered = {
      ...CLOCK_TOWER,
      names: {
        en: { value: 'Clock Tower' }, // model-asserted: no stamp
        'el-Latn': CLOCK_TOWER.names['el-Latn'],
      },
    }
    // Guard first (this is what the layer does), then render.
    const element = buildMarkerElement(sanitiseSite(tampered) as never)
    peek(element, 'pointerenter')
    expect(element.textContent).not.toMatch(/Clock Tower/)
    expect(element.querySelector('.siyur-value__text')?.textContent).toBe('Roloi')
    expect(element.querySelector('.siyur-chip')).not.toBeNull()
  })
})

/* --------------------------------- WCAG 2.2 SC 4.1.2 (Name, Role, Value) --- */

/** `CLOCK_TOWER` with every stamped name gone — the unnamed-site case. */
const UNNAMED_OSM = { ...CLOCK_TOWER, names: {} }
/** Same, but its location is stamped `overture` — proves the chip is read, not hardcoded. */
const UNNAMED_OVERTURE = { ...PALACE, names: {} }

const ariaLabelOf = (site: unknown, options = {}): string =>
  buildMarkerElement(site as never, options).getAttribute('aria-label') ?? ''

describe('a focusable marker always has an accessible name (WCAG 2.2 SC 4.1.2)', () => {
  it('never leaves an unnamed site a focusable role="button" with no name', () => {
    const element = buildMarkerElement(UNNAMED_OSM as never)
    // Still keyboard-reachable: the popup is the full cited fact list (its
    // LOCATION row included), so it must not degrade to pointer-only.
    expect(element.tabIndex).toBe(0)
    expect(element.getAttribute('role')).toBe('button')
    expect(element.getAttribute('aria-label')?.trim()).toBeTruthy()
  })

  it('names it as an unnamed place instead of inventing a place name', () => {
    const label = ariaLabelOf(UNNAMED_OSM)
    expect(label).toMatch(/unnamed place/i)
    // Nothing from `names` leaks in — there is no stamped name to render.
    expect(label).not.toMatch(/Ρολόι|Roloi/)
  })

  it('keeps the location stamp co-present with the coordinates it renders', () => {
    // The coordinates ARE a displayed value, so the location's own chip text
    // travels with them inside the same accessible name (ADR-0019, rule 5).
    expect(ariaLabelOf(UNNAMED_OSM)).toMatch(/36\.44510, 28\.22350/)
    expect(ariaLabelOf(UNNAMED_OSM)).toContain(CLOCK_TOWER_CHIP)
  })

  it('reads each record’s own location stamp, never a hardcoded credit', () => {
    const label = ariaLabelOf(UNNAMED_OVERTURE)
    expect(label).toMatch(/36\.44430, 28\.22470/)
    expect(label).toContain('OVERTURE · CDLA-Permissive-2.0')
    expect(label).not.toMatch(/OpenStreetMap/)
  })

  it('holds in labelled (sparse) mode too, not only in dot mode', () => {
    const element = buildMarkerElement(UNNAMED_OSM as never, { labelled: true })
    expect(element.getAttribute('aria-label')?.trim()).toBeTruthy()
    expect(element.querySelector('.siyur-value')).toBeNull()
  })

  it('leaves a named site’s accessible name exactly as it was — name + chip', () => {
    const label = ariaLabelOf(CLOCK_TOWER)
    expect(label).toMatch(/Roloi/)
    expect(label).toContain(CLOCK_TOWER_CHIP)
    // A named site does not pick up the coordinate fallback.
    expect(label).not.toMatch(/unnamed place/i)
  })
})

describe('popup content is built on first open, not for every marker up front', () => {
  it('holds no popup DOM until the popup opens', () => {
    createSiteMarker(PALACE as never, fakeMap())
    const popup = stub.markers[0]?.popup as PopupLike
    expect(popup.content).toBeNull()
    expect(popup.builds).toBe(0)
  })

  it('builds the fully-chipped fact list on open, once', () => {
    createSiteMarker(PALACE as never, fakeMap())
    const popup = stub.markers[0]?.popup as PopupLike
    popup.fire('open')
    const content = popup.content!
    const rows = [...content.querySelectorAll('.siyur-popup__row')]
    expect(rows.length).toBeGreaterThan(0)
    for (const row of rows) expect(row.querySelector('.siyur-chip')).not.toBeNull()
    expect(content.textContent).toMatch(/Palace of the Grand Master/)

    popup.fire('open')
    expect(popup.builds).toBe(1)
  })
})

/* ----------------------------------------------------------------- layer --- */

describe('SitesLayer', () => {
  it('places one marker per site at its stamped [lon, lat]', async () => {
    const map = fakeMap()
    const layer = new SitesLayer(map, null, { fetchImpl: okFetch(RESPONSE) })
    await layer.refresh()

    expect(stub.markers).toHaveLength(2)
    expect(stub.markers.every((m) => m.added)).toBe(true)
    expect(stub.markers[0]?.lngLat).toEqual([28.2247, 36.4443])
    expect(stub.markers[1]?.lngLat).toEqual([28.2235, 36.4451])
    expect(stub.markers[0]?.opts.element?.textContent).toMatch(/Palace of the Grand Master/)
    expect(layer.markerCount).toBe(2)
  })

  it('drops a site whose location is unstamped instead of guessing one', async () => {
    const tampered = {
      sites: [
        PALACE,
        { ...CLOCK_TOWER, location: { value: { type: 'Point', coordinates: [28.22, 36.44] } } },
      ],
      attribution: [],
    }
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: okFetch(tampered) })
    await layer.refresh()
    expect(stub.markers).toHaveLength(1)
    expect(stub.markers[0]?.opts.element?.dataset.siteId).toBe('6f1c-uuid')
  })

  it('drives the ODbL control from the response’s attribution[] (T044)', async () => {
    const control = new OdblAttributionControl()
    const element = control.onAdd({} as unknown as MapLibreMap)
    const layer = new SitesLayer(fakeMap(), control, { fetchImpl: okFetch(RESPONSE) })
    await layer.refresh()

    expect(element.dataset.odblRequired).toBe('true')
    expect(element.textContent).toMatch(/OpenStreetMap contributors/)
  })

  it('clears the previous markers on the next refresh', async () => {
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: okFetch(RESPONSE) })
    await layer.refresh()
    const first = [...stub.markers]
    await layer.refresh()
    expect(first.every((m) => m.removed)).toBe(true)
    expect(layer.markerCount).toBe(2)
  })

  it('refetches on moveend and stops after destroy()', async () => {
    const map = fakeMap()
    const spy = okFetch(RESPONSE)
    const layer = new SitesLayer(map, null, { fetchImpl: spy })
    layer.start()
    await Promise.resolve()
    expect(map.handlers.moveend).toHaveLength(1)

    layer.destroy()
    expect(map.handlers.moveend).toHaveLength(0)
    expect(layer.markerCount).toBe(0)
  })

  it('labels every marker while the viewport stays sparse', async () => {
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: okFetch(RESPONSE) })
    await layer.refresh()
    expect(layer.labelsVisible).toBe(true)
    for (const marker of stub.markers) {
      expect(marker.opts.element?.querySelector('.siyur-chip')).not.toBeNull()
    }
  })

  it('falls back to dots once the response exceeds the density limit', async () => {
    const crowded = { sites: manySites(SITE_LABEL_DENSITY_LIMIT + 1), attribution: [] }
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: okFetch(crowded) })
    await layer.refresh()

    expect(layer.markerCount).toBe(SITE_LABEL_DENSITY_LIMIT + 1)
    expect(layer.labelsVisible).toBe(false)
    for (const marker of stub.markers) {
      const element = marker.opts.element!
      expect(element.querySelector('.siyur-marker__pin')).not.toBeNull()
      expect(element.querySelector('.siyur-chip')).toBeNull()
    }
    // …and the attribution is one interaction away on every one of them.
    const first = stub.markers[0]!.opts.element!
    first.dispatchEvent(new Event('pointerenter'))
    expect(first.querySelector('.siyur-chip')?.textContent).toBe(CLOCK_TOWER_CHIP)
  })

  it('honours an explicit labelDensityLimit', async () => {
    const layer = new SitesLayer(fakeMap(), null, {
      fetchImpl: okFetch(RESPONSE),
      labelDensityLimit: 1,
    })
    await layer.refresh()
    expect(layer.labelsVisible).toBe(false)
  })

  it('still credits ODbL in a dense viewport — density never dilutes attribution', async () => {
    const control = new OdblAttributionControl()
    const element = control.onAdd({} as unknown as MapLibreMap)
    const crowded = {
      sites: manySites(SITE_LABEL_DENSITY_LIMIT + 1),
      attribution: ['© OpenStreetMap contributors'],
    }
    const layer = new SitesLayer(fakeMap(), control, { fetchImpl: okFetch(crowded) })
    await layer.refresh()

    expect(layer.labelsVisible).toBe(false)
    expect(element.dataset.odblRequired).toBe('true')
    expect(element.textContent).toMatch(/OpenStreetMap contributors/)
  })

  it('drops an unstamped value in a dense viewport exactly as in a sparse one', async () => {
    const sites = manySites(SITE_LABEL_DENSITY_LIMIT + 1)
    // One record's preferred name arrives unstamped; it must never surface.
    sites[0] = { ...sites[0]!, names: { en: { value: 'Invented Tower' } } } as never
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: okFetch({ sites, attribution: [] }) })
    await layer.refresh()

    const element = stub.markers[0]!.opts.element!
    element.dispatchEvent(new Event('pointerenter'))
    expect(element.textContent).not.toMatch(/Invented Tower/)
    expect(element.querySelector('.siyur-value')).toBeNull()
    // The unstamped name must not reach the accessible name either — that is a
    // display surface too, and it is the one an AT user actually receives.
    const ariaLabel = element.getAttribute('aria-label') ?? ''
    expect(ariaLabel).toBeTruthy()
    expect(ariaLabel).not.toMatch(/Invented Tower/)
  })

  it('reports a failed request instead of rendering anything', async () => {
    const onError = vi.fn()
    const failing = vi.fn(async () => new Response('', { status: 401 })) as unknown as typeof fetch
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: failing, onError })
    expect(await layer.refresh()).toBeNull()
    expect(onError).toHaveBeenCalledOnce()
    expect(stub.markers).toHaveLength(0)
  })
})

/* -------------------------------------------------- T052 · reuse bridge --- */

describe('mountAreaReuse (T052 / US2 / FR-006)', () => {
  const covered = {
    area_id: 'area-1',
    polygon: null,
    coverage: {
      known_site_count: 42,
      covered: true,
      stalest_observed_at: '2026-07-10',
      refresh_available: true,
    },
  }

  it('shows the existing cited sites via GET /sites and researches nothing', async () => {
    const spy = okFetch(RESPONSE)
    const requestResearch = vi.fn()
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: spy })
    const container = document.createElement('div')

    const surface = mountAreaReuse(layer, container, { requestResearch })
    expect(await surface.apply(covered)).toBe('reuse')

    // Existing data is rendered…
    expect(layer.markerCount).toBe(2)
    // …from GET /sites, which the contract defines as read-only…
    const [url] = (spy as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [string]
    expect(url).toMatch(/^\/sites\?bbox=/)
    // …and no research pass was started (FR-006 / SC-003).
    expect(requestResearch).not.toHaveBeenCalled()
    expect(container.querySelector('[data-action="refresh"]')).not.toBeNull()
  })

  it('requests research only once the user clicks refresh', async () => {
    const requestResearch = vi.fn()
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: okFetch(RESPONSE) })
    const container = document.createElement('div')
    await mountAreaReuse(layer, container, { requestResearch }).apply(covered)

    expect(requestResearch).not.toHaveBeenCalled()
    container.querySelector<HTMLButtonElement>('[data-action="refresh"]')?.click()
    expect(requestResearch).toHaveBeenCalledWith({ area_id: 'area-1', force_refresh: true })
  })
})
