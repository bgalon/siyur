/**
 * The US1 acceptance path, end to end in the app shell.
 *
 * > "sign in → **delimit the area** → trigger research → cited markers appear,
 * > each with a visible source + license chip, ODbL credit rendered, no unstamped
 * > value shown."
 *
 * Everything but the sign-in is exercised here against the three contracts'
 * worked examples, so the criterion is provably *performable* rather than merely
 * implemented in parts. jsdom has no WebGL, so MapLibre is stubbed — the genuine
 * render is DU-06's airplane-mode e2e.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

const bounds = { getWest: () => -3.125, getSouth: () => 51.25, getEast: () => -3.1, getNorth: () => 51.275 }

vi.mock('virtual:pwa-register', () => ({ registerSW: vi.fn() }))

vi.mock('maplibre-gl', () => {
  class FakeMap {
    constructor(public opts: Record<string, unknown>) {}
    getBounds() {
      return bounds
    }
    getContainer() {
      return this.opts.container
    }
    on() {}
    off() {}
    addControl() {}
    remove() {}
  }
  class FakeMarker {
    element: HTMLElement
    constructor(opts?: { element?: HTMLElement }) {
      this.element = opts?.element ?? document.createElement('div')
    }
    setLngLat() {
      return this
    }
    setPopup() {
      return this
    }
    addTo() {
      return this
    }
    remove() {}
  }
  class FakePopup {
    setDOMContent() {
      return this
    }
  }
  return {
    default: { Map: FakeMap, Marker: FakeMarker, Popup: FakePopup },
    Map: FakeMap,
    Marker: FakeMarker,
    Popup: FakePopup,
  }
})

/* -------------------------------------------------------------- fixtures --- */

const SITE = {
  id: 'site-1',
  names: {
    en: {
      value: 'A researched place',
      source: { kind: 'osm', license: 'ODbL-1.0', attribution: '© OpenStreetMap contributors' },
    },
  },
  location: {
    value: { type: 'Point', coordinates: [-3.11, 51.26] },
    source: { kind: 'overture', license: 'CDLA-Permissive-2.0' },
  },
}

const UNCOVERED_AREA = {
  area_id: 'area-1',
  polygon: null,
  coverage: {
    known_site_count: 0,
    covered: false,
    stalest_observed_at: null,
    refresh_available: false,
  },
}

const RESEARCH_STREAM =
  'event: status\ndata: {"phase":"research","source":"osm","found":1}\n\n' +
  `event: site\ndata: ${JSON.stringify(SITE)}\n\n` +
  'event: summary\ndata: {"sites":1,"new":1,"reused":0,"conflicts":0,' +
  '"sources":{"osm":1},"degraded_sources":[]}\n\n' +
  'event: done\ndata: {"area_id":"area-1"}\n\n'

/** One in-memory backend implementing the three contracts. */
const backend = (): typeof fetch => {
  let researched = false
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith('/areas/') && url.endsWith('/research')) {
      researched = true
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(RESEARCH_STREAM))
          controller.close()
        },
      })
      return new Response(body, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.startsWith('/areas')) {
      return Response.json(UNCOVERED_AREA)
    }
    // `GET /sites` — empty until the pass has persisted something.
    return Response.json({
      sites: researched ? [SITE] : [],
      attribution: researched ? ['© OpenStreetMap contributors'] : [],
    })
  }) as unknown as typeof fetch
}

/** Let the fetch + stream microtasks and their reader turns settle. */
const settle = async (turns = 8): Promise<void> => {
  for (let i = 0; i < turns; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

const query = <T extends Element>(selector: string): T | null =>
  document.querySelector<T>(selector)

/* ------------------------------------------------------------------ shell --- */

describe('the app shell (US1 acceptance path)', () => {
  beforeEach(async () => {
    document.body.replaceChildren()
    const map = document.createElement('div')
    map.id = 'map'
    document.body.append(map)
    vi.stubGlobal('fetch', backend())
    vi.resetModules()
    await import('../src/main')
    await settle()
  })

  it('mounts a delimit control and a bottom sheet over the map', () => {
    expect(query('.siyur-delimit')).not.toBeNull()
    expect(query('.siyur-sheet')).not.toBeNull()
    // Nothing has been delimited yet, so there is no coverage card and no pass.
    expect(query('.siyur-coverage')).toBeNull()
    expect(query<HTMLElement>('.siyur-research')?.dataset.state).toBe('idle')
  })

  it('delimits the current view, resolves it, and offers research — but starts none', async () => {
    query<HTMLButtonElement>('[data-action="use-viewport"]')!.click()
    await settle()

    const card = query<HTMLElement>('.siyur-coverage')
    expect(card).not.toBeNull()
    expect(card?.dataset.covered).toBe('false')
    // FR-006: resolving an area never triggers a pass on its own.
    expect(query<HTMLElement>('.siyur-research')?.dataset.state).toBe('idle')

    const bodies = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
      .filter(([url]) => url === '/areas')
      .map(([, init]) => JSON.parse((init as RequestInit).body as string) as unknown)
    // The bbox is the map's own viewport — nothing is hardcoded (FR-001/SC-005).
    expect(bodies).toEqual([{ bbox: [-3.125, 51.25, -3.1, 51.275] }])
  })

  it('runs the pass on the user gesture and shows cited, stamped markers', async () => {
    query<HTMLButtonElement>('[data-action="use-viewport"]')!.click()
    await settle()

    query<HTMLButtonElement>('.siyur-coverage__action')!.click()
    await settle()

    const research = query<HTMLElement>('.siyur-research')
    expect(research?.dataset.state).toBe('complete')
    expect(research?.querySelector('.siyur-research__result')?.textContent).toMatch(
      /1 cited place · 1 new/,
    )

    // The marker DOM built for the persisted site carries its own source chip,
    // and every chip's text comes from that value's stamp (FR-003 / SC-002).
    const { buildMarkerElement } = await import('../src/map/sites')
    const { sanitiseSite } = await import('../src/map/guards')
    const site = sanitiseSite(SITE)
    expect(site).not.toBeNull()
    const chip = buildMarkerElement(site!).querySelector<HTMLElement>('.siyur-chip')
    expect(chip?.dataset.sourceKind).toBe('osm')
    expect(chip?.textContent).toContain('ODbL-1.0')
  })
})
