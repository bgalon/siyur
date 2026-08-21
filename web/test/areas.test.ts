/**
 * T052 — the US2 reuse surface (FR-006 / SC-003).
 *
 * `POST /areas` does not exist yet; the CONTRACT is the interface, so the fetch
 * is mocked with `specs/001-research-cited-sites/contracts/areas.md`'s own
 * worked example. The load-bearing assertions are the negative ones: a covered
 * area must produce **no** research call on its own.
 */

import { describe, it, expect, vi } from 'vitest'

import {
  AreaRequestError,
  AreaResponseError,
  AreaReuseSurface,
  buildCoverageCard,
  coverageAction,
  describeStaleness,
  researchPath,
  resolveAndApply,
  resolveArea,
} from '../src/map/areas'
import { sanitiseAreaResolution, sanitiseCoverage, sanitisePolygon } from '../src/map/guards'

/* ------------------------------------------------------------- fixtures --- */

/** The contract's worked example, verbatim. */
const COVERED_BODY = {
  area_id: '9c1e-uuid',
  polygon: {
    type: 'Polygon',
    coordinates: [
      [
        [28.216, 36.44],
        [28.232, 36.44],
        [28.232, 36.451],
        [28.216, 36.451],
        [28.216, 36.44],
      ],
    ],
  },
  coverage: {
    known_site_count: 42,
    covered: true,
    stalest_observed_at: '2026-07-10',
    refresh_available: true,
  },
}

const UNCOVERED_BODY = {
  area_id: 'f0a2-uuid',
  polygon: null,
  coverage: {
    known_site_count: 0,
    covered: false,
    stalest_observed_at: null,
    refresh_available: false,
  },
}

const covered = () => sanitiseAreaResolution(COVERED_BODY)!
const uncovered = () => sanitiseAreaResolution(UNCOVERED_BODY)!

const okFetch = (body: unknown): typeof fetch =>
  vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch

const NOW = new Date('2026-08-01T00:00:00Z')

/* ---------------------------------------------------------------- guard --- */

describe('sanitiseCoverage', () => {
  it('passes the contract fixture through intact', () => {
    expect(sanitiseCoverage(COVERED_BODY.coverage)).toEqual(COVERED_BODY.coverage)
  })

  it('never infers coverage from the count alone', () => {
    // A count without the server's own `covered` verdict is not coverage —
    // the client reads the stamp, it does not derive one.
    expect(sanitiseCoverage({ known_site_count: 42 }).covered).toBe(false)
    expect(sanitiseCoverage(undefined)).toEqual({
      known_site_count: 0,
      covered: false,
      stalest_observed_at: null,
      refresh_available: false,
    })
  })

  it('always offers a refresh for a covered area (FR-006)', () => {
    // The contract says refresh_available is always true when covered; FR-006
    // requires the option to exist, so a covered area cannot lose it.
    expect(sanitiseCoverage({ covered: true, refresh_available: false }).refresh_available).toBe(
      true,
    )
  })

  it('drops a junk count and a blank staleness date rather than rendering them', () => {
    const coverage = sanitiseCoverage({
      known_site_count: -3,
      covered: true,
      stalest_observed_at: '   ',
    })
    expect(coverage.known_site_count).toBe(0)
    expect(coverage.stalest_observed_at).toBeNull()
  })
})

describe('sanitisePolygon / sanitiseAreaResolution', () => {
  it('keeps a valid EPSG:4326 ring', () => {
    expect(sanitisePolygon(COVERED_BODY.polygon)).toEqual(COVERED_BODY.polygon)
  })

  it.each([
    ['out-of-range lon', [[[999, 36.44], [28.23, 36.44], [28.23, 36.45], [999, 36.44]]]],
    ['too few vertices', [[[28.21, 36.44], [28.23, 36.44]]]],
    ['a non-polygon', undefined],
  ])('refuses to repair %s — returns null', (_label, coordinates) => {
    expect(sanitisePolygon({ type: 'Polygon', coordinates })).toBeNull()
  })

  it('returns null without an area_id — there is nothing to reuse or refresh', () => {
    expect(sanitiseAreaResolution({ coverage: COVERED_BODY.coverage })).toBeNull()
  })
})

/* ----------------------------------------------------------------- fetch --- */

describe('resolveArea', () => {
  it('POSTs the user-delimited area with the bearer token', async () => {
    const spy = okFetch(COVERED_BODY)
    const area = { bbox: [28.216, 36.44, 28.232, 36.451] as const }
    await resolveArea({ area, token: 'tok-123', fetchImpl: spy })

    const [url, init] = (spy as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ]
    expect(url).toBe('/areas')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123')
    expect(JSON.parse(init.body as string)).toEqual({ bbox: [28.216, 36.44, 28.232, 36.451] })
  })

  it.each([401, 404, 422])('raises AreaRequestError on %i', async (status) => {
    const failing = vi.fn(async () => new Response('', { status })) as unknown as typeof fetch
    await expect(resolveArea({ area: { name: 'x' }, fetchImpl: failing })).rejects.toBeInstanceOf(
      AreaRequestError,
    )
  })

  it('raises AreaResponseError on a body with no area_id', async () => {
    await expect(
      resolveArea({ area: { name: 'x' }, fetchImpl: okFetch({ coverage: {} }) }),
    ).rejects.toBeInstanceOf(AreaResponseError)
  })

  it('builds the research path from contracts/research.md', () => {
    expect(researchPath('9c1e-uuid')).toBe('/areas/9c1e-uuid/research')
  })
})

/* ------------------------------------------------------------ staleness --- */

describe('describeStaleness', () => {
  it('surfaces how old the stalest covered record is', () => {
    expect(describeStaleness('2026-07-10', NOW)).toBe('2026-07-10 · 22 days ago')
    expect(describeStaleness('2026-07-31', NOW)).toBe('2026-07-31 · 1 day ago')
    expect(describeStaleness('2026-08-01', NOW)).toBe('2026-08-01 · today')
  })

  it('echoes an unparseable stamp verbatim instead of inventing a date', () => {
    expect(describeStaleness('sometime last week', NOW)).toBe('sometime last week')
    expect(describeStaleness(null, NOW)).toBeNull()
  })
})

/* ------------------------------------------------------------------ card --- */

describe('buildCoverageCard', () => {
  it('shows the count, the staleness and a refresh button for a covered area', () => {
    const card = buildCoverageCard(covered(), { now: () => NOW })
    expect(card.dataset.covered).toBe('true')
    expect(card.dataset.refreshAvailable).toBe('true')
    expect(card.dataset.stalestObservedAt).toBe('2026-07-10')
    expect(card.textContent).toMatch(/42 cited places already researched/)
    expect(card.textContent).toMatch(/2026-07-10 · 22 days ago/)

    const button = card.querySelector<HTMLButtonElement>('.siyur-coverage__action')!
    expect(button.dataset.action).toBe('refresh')
    expect(button.disabled).toBe(false)
  })

  it('asks for force_refresh=true from a covered area, and only on click', () => {
    const requestResearch = vi.fn()
    const card = buildCoverageCard(covered(), { requestResearch })
    expect(requestResearch).not.toHaveBeenCalled()

    card.querySelector<HTMLButtonElement>('.siyur-coverage__action')!.click()
    expect(requestResearch).toHaveBeenCalledExactlyOnceWith({
      area_id: '9c1e-uuid',
      force_refresh: true,
    })
  })

  it('offers a first research pass — not a refresh — for an uncovered area', () => {
    const requestResearch = vi.fn()
    const card = buildCoverageCard(uncovered(), { requestResearch })
    expect(card.dataset.covered).toBe('false')
    expect(card.textContent).toMatch(/No cited places here yet/)

    const button = card.querySelector<HTMLButtonElement>('.siyur-coverage__action')!
    expect(button.dataset.action).toBe('research')
    // Still user-driven: an uncovered area does not research itself either.
    expect(requestResearch).not.toHaveBeenCalled()
    button.click()
    expect(requestResearch).toHaveBeenCalledWith({ area_id: 'f0a2-uuid', force_refresh: false })
  })

  it('renders no staleness line when the server sent no date', () => {
    const card = buildCoverageCard(uncovered())
    expect(card.querySelector('.siyur-coverage__staleness')).toBeNull()
    expect(card.dataset.stalestObservedAt).toBeUndefined()
  })
})

/* ------------------------------------------------------------ controller --- */

describe('AreaReuseSurface', () => {
  const mount = (options = {}) => {
    const container = document.createElement('div')
    return { container, surface: new AreaReuseSurface(container, options) }
  }

  it('shows existing sites and starts NO research for a covered area (FR-006)', async () => {
    const showExistingSites = vi.fn()
    const requestResearch = vi.fn()
    const { container, surface } = mount({ showExistingSites, requestResearch })

    expect(await surface.apply(covered())).toBe('reuse')
    expect(showExistingSites).toHaveBeenCalledOnce()
    expect(requestResearch).not.toHaveBeenCalled()
    expect(container.querySelector('.siyur-coverage')).not.toBeNull()
  })

  it('does not fetch existing sites for an uncovered area, and still researches nothing', async () => {
    const showExistingSites = vi.fn()
    const requestResearch = vi.fn()
    const { surface } = mount({ showExistingSites, requestResearch })

    expect(await surface.apply(uncovered())).toBe('research')
    expect(showExistingSites).not.toHaveBeenCalled()
    expect(requestResearch).not.toHaveBeenCalled()
  })

  it('replaces the previous card rather than stacking coverage cards', async () => {
    const { container, surface } = mount({})
    await surface.apply(covered())
    await surface.apply(uncovered())
    expect(container.querySelectorAll('.siyur-coverage')).toHaveLength(1)
    expect(container.querySelector<HTMLElement>('.siyur-coverage')?.dataset.covered).toBe('false')

    surface.destroy()
    expect(container.querySelector('.siyur-coverage')).toBeNull()
    expect(surface.element).toBeNull()
  })

  it('reports a failing sites read instead of falling back to research', async () => {
    const onError = vi.fn()
    const requestResearch = vi.fn()
    const { surface } = mount({
      showExistingSites: () => {
        throw new Error('401')
      },
      requestResearch,
      onError,
    })

    expect(await surface.apply(covered())).toBe('reuse')
    expect(onError).toHaveBeenCalledOnce()
    expect(requestResearch).not.toHaveBeenCalled()
  })

  it('resolveAndApply applies nothing once the caller has moved on', async () => {
    // `Use this view` can pre-empt a 65s name search (R-02), so two resolutions can be racing
    // and the slow one can land LAST. The apply is the first irreversible step, so it is the
    // one that has to ask. Without this the escape hatch becomes a delayed hijack: the area
    // the user abandoned quietly overwrites the one they chose.
    const requestResearch = vi.fn()
    const showExistingSites = vi.fn()
    const { container, surface } = mount({ showExistingSites, requestResearch })

    const resolution = await resolveAndApply(
      surface,
      { area: { name: 'Rhodes medieval old town' }, fetchImpl: okFetch(COVERED_BODY) },
      () => false, // superseded while the request was in flight
    )

    // The answer still comes back — the caller decides what to do with it…
    expect(resolution.area_id).toBe('9c1e-uuid')
    // …but nothing was written to the surface on its behalf.
    expect(showExistingSites).not.toHaveBeenCalled()
    expect(requestResearch).not.toHaveBeenCalled()
    expect(container.querySelector('[data-action="refresh"]')).toBeNull()
  })

  it('resolveAndApply reuses a covered area end to end without researching', async () => {
    const requestResearch = vi.fn()
    const showExistingSites = vi.fn()
    const { container, surface } = mount({ showExistingSites, requestResearch })

    const resolution = await resolveAndApply(surface, {
      area: { name: 'Rhodes medieval old town' },
      fetchImpl: okFetch(COVERED_BODY),
    })

    expect(coverageAction(resolution)).toBe('reuse')
    expect(showExistingSites).toHaveBeenCalledOnce()
    expect(requestResearch).not.toHaveBeenCalled()
    expect(container.querySelector('[data-action="refresh"]')).not.toBeNull()
  })
})
