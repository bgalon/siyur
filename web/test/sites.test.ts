import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { Map as MapLibreMap } from 'maplibre-gl'

// jsdom has no WebGL, so MapLibre's Marker/Popup are stubbed. The backend
// `GET /sites` does not exist yet either — the CONTRACT is the interface, so the
// fetch is mocked with the contract's own worked example
// (specs/001-research-cited-sites/contracts/sites.md).
const stub = vi.hoisted(() => ({
  markers: [] as {
    opts: { element?: HTMLElement }
    lngLat: [number, number] | null
    popup: { content: HTMLElement | null } | null
    added: boolean
    removed: boolean
  }[],
}))

vi.mock('maplibre-gl', () => {
  class FakePopup {
    content: HTMLElement | null = null
    constructor(public opts: Record<string, unknown>) {}
    setDOMContent(element: HTMLElement) {
      this.content = element
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
  SitesLayer,
  SitesRequestError,
  buildMarkerElement,
  buildPopupContent,
  displayName,
  displayNameOrder,
  fetchSites,
  formatBbox,
} = await import('../src/map/sites')
const { OdblAttributionControl } = await import('../src/map/attribution')

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

/* ------------------------------------------------------------- rendering --- */

describe('marker rendering', () => {
  it('renders the display name with its own attribution chip', () => {
    const element = buildMarkerElement(CLOCK_TOWER as never)
    expect(element.dataset.siteId).toBe('a20e-uuid')
    expect(element.querySelector('.siyur-value__text')?.textContent).toBe('Roloi')
    expect(element.querySelector('.siyur-chip')?.textContent).toBe(
      'OSM · ODbL-1.0 · © OpenStreetMap contributors',
    )
  })

  it('renders a pin but no name row when the record carries no stamped name', () => {
    const element = buildMarkerElement({ ...CLOCK_TOWER, names: {} } as never)
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

  it('reports a failed request instead of rendering anything', async () => {
    const onError = vi.fn()
    const failing = vi.fn(async () => new Response('', { status: 401 })) as unknown as typeof fetch
    const layer = new SitesLayer(fakeMap(), null, { fetchImpl: failing, onError })
    expect(await layer.refresh()).toBeNull()
    expect(onError).toHaveBeenCalledOnce()
    expect(stub.markers).toHaveLength(0)
  })
})
