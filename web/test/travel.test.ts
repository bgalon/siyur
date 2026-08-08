import { describe, expect, it } from 'vitest'

import { openBundle } from '../src/bundle'
import {
  OffRouteRecovery,
  createNeedsConnectivity,
  createPlaceCard,
  createWithheldBlock,
  haversineMetres,
  loadTravelBundle,
  parseItinerary,
  parseNarrations,
  parseSites,
  parseWalkGraph,
  renderDay,
  renderStory,
  withheldFieldKind,
} from '../src/travel'
import {
  ITINERARY,
  NARRATIONS,
  SITES,
  SITE_A,
  SITE_B,
  WALK_GRAPH,
  buildSyntheticBundle,
} from './fixtures/bundle'

const loaded = async () => {
  const { storage, manifest } = await buildSyntheticBundle()
  const opened = await openBundle({ storage })
  expect(opened.ok).toBe(true)
  const travel = await loadTravelBundle(storage, manifest)
  if (!travel) throw new Error('the synthetic bundle failed to load')
  return travel
}

describe('the day renders from the bundle alone (T051)', () => {
  it('loads itinerary, sites, narration, legs and attribution out of OPFS paths', async () => {
    const travel = await loaded()
    expect(travel.itinerary.date).toBe('2026-08-12')
    expect(travel.itinerary.stops).toHaveLength(2)
    expect(travel.sites.size).toBe(2)
    expect(travel.narrations.get(SITE_B)).toHaveLength(1)
    expect(travel.legs).toHaveLength(1)
    expect(travel.attribution).toMatch(/OpenStreetMap contributors/)
  })

  it('renders the timeline in plan order, legs included', async () => {
    const travel = await loaded()
    const day = renderDay(travel)
    const rows = [...day.querySelectorAll('.siyur-timeline > *')]
    expect(rows.map((r) => r.className)).toEqual([
      'siyur-timeline__item',
      'siyur-leg',
      'siyur-timeline__item',
    ])
    expect((rows[0] as HTMLElement).dataset.stopOrder).toBe('0')
    expect((rows[1] as HTMLElement).dataset.legId).toBe('leg-0')
  })

  it('renders planned times as area-local wall clock, never through Date', async () => {
    // A device still on the departure timezone must show the same 09:30 the plan froze.
    const travel = await loaded()
    const day = renderDay(travel)
    expect(day.querySelector('.siyur-timeline__when')?.textContent).toBe('09:30 · 60 min')
  })
})

describe('ADR-0019 — co-presence is discharged by layout on this surface', () => {
  /**
   * The invariant the whole decision rests on, asserted structurally rather than by
   * naming the values this fixture happens to carry: **every rendered value element
   * contains its own chip.** A future field added to the place card is covered by this
   * test the day it is added.
   */
  it('every rendered value element contains its own attribution chip', async () => {
    const travel = await loaded()
    const day = renderDay(travel)
    const values = [...day.querySelectorAll('.siyur-value')]
    expect(values.length).toBeGreaterThan(0)
    for (const value of values) {
      expect(value.querySelectorAll('.siyur-chip')).toHaveLength(1)
    }
  })

  it('the narration text carries its own CC-BY-SA article credit, in the same element', async () => {
    const travel = await loaded()
    const day = renderDay(travel)
    const story = day.querySelector('.siyur-story .siyur-value') as HTMLElement
    expect(story.querySelector('.siyur-value__text')?.textContent).toMatch(/cobbled street/)
    const chip = story.querySelector('.siyur-chip') as HTMLElement
    expect(chip.dataset.license).toBe('CC-BY-SA-4.0')
    expect(chip.textContent).toMatch(/Wikivoyage/)
  })

  it('a walking leg’s distance and time each carry the leg’s ODbL stamp', async () => {
    const travel = await loaded()
    const day = renderDay(travel)
    const chips = [...day.querySelectorAll('.siyur-leg .siyur-chip')] as HTMLElement[]
    expect(chips).toHaveLength(2)
    for (const chip of chips) {
      expect(chip.dataset.sourceKind).toBe('osm')
      expect(chip.dataset.license).toBe('ODbL-1.0')
      expect(chip.textContent).toMatch(/© OpenStreetMap contributors/)
    }
  })

  it('a story with no source renders NOTHING — never uncredited prose (SC-010)', async () => {
    // The one thing CC BY-SA does not permit. `renderStory` must return null, not a
    // paragraph missing a chip.
    expect(
      renderStory(
        { text_by_lang: { en: 'Adapted prose with no article behind it.' } } as never,
        'en',
      ),
    ).toBeNull()
  })

  it('MUTATION GUARD: a value rendered outside the chip funnel would be caught', async () => {
    // Proves the structural assertion above can fail. A hand-built element carrying the
    // value class but no chip is exactly the "name without chip" regression ADR-0019's
    // pulled-forward guard 1 exists to stop.
    const travel = await loaded()
    const day = renderDay(travel)
    const smuggled = document.createElement('span')
    smuggled.className = 'siyur-value'
    smuggled.textContent = 'Palace of the Grand Master'
    day.append(smuggled)

    const offenders = [...day.querySelectorAll('.siyur-value')].filter(
      (v) => v.querySelectorAll('.siyur-chip').length !== 1,
    )
    expect(offenders).toHaveLength(1)
  })

  /**
   * ADR-0019's pulled-forward item 1, for the package this slice adds.
   *
   * The ADR asks for an AST guard that **no module outside `attribution-chip.ts` reads
   * `SourcedValue.value` for display** — "the one property the whole invariant rests on
   * and the only one with nothing behind it but review". The ADR assigns it to the
   * Python genericity scan's neighbourhood, which does not scan `web/` at all
   * (`evals/test_genericity.py` walks the Python packages only). This is the
   * TypeScript-side half, scoped to the modules DU-06 adds; the repo-wide one is still
   * owed and is not this slice's file to write.
   *
   * The DOM assertions above prove the *current* code funnels correctly. This proves the
   * *next* module cannot quietly stop.
   */
  it('no module in src/travel reads a SourcedValue payload directly', async () => {
    const { readdirSync, readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    // `import.meta.url` is not a `file:` URL under the jsdom environment, so the walk
    // is rooted at the vitest root (`web/`). The length assertion below is the tripwire
    // that catches a wrong root rather than passing vacuously over zero files.
    const dir = join(process.cwd(), 'src', 'travel')
    const files = readdirSync(dir).filter((name) => name.endsWith('.ts'))
    expect(files.length).toBeGreaterThan(4) // the walk found something, not nothing

    const offenders: string[] = []
    for (const name of files) {
      // `types.ts` DECLARES `readonly value: T`; declaring it is not reading it.
      if (name === 'types.ts') continue
      const source = readFileSync(join(dir, name), 'utf8')
      // `\b` after `value` keeps `Object.values(...)` — a different method — out of it.
      for (const [index, line] of source.split('\n').entries()) {
        if (/\.value\b/.test(line)) offenders.push(`${name}:${index + 1} ${line.trim()}`)
      }
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })

  it('the plan credit states that times are user-owned rather than leaving a gap', async () => {
    const travel = await loaded()
    const credit = renderDay(travel).querySelector('.siyur-plan-credit') as HTMLElement
    expect(credit.dataset.planSource).toBe('user-owned')
    expect(credit.textContent).toMatch(/your own plan/i)
  })
})

describe('withheld content is "needs connectivity" — never blank, never an error (FR-021)', () => {
  it('renders an affordance for each withheld kind, with its reason', async () => {
    const travel = await loaded()
    const day = renderDay(travel)
    const notices = [...day.querySelectorAll('.siyur-withheld')] as HTMLElement[]
    expect(notices.length).toBe(2)
    for (const notice of notices) {
      expect(notice.textContent).toMatch(/needs connectivity/)
      // Not an error: no alert role, no error class, and the text does not say "error".
      expect(notice.getAttribute('role')).toBeNull()
      expect(notice.textContent).not.toMatch(/error|failed|unavailable\b/i)
      expect(notice.textContent?.trim()).not.toBe('')
    }
    expect(notices.map((n) => n.dataset.withheldReason)).toEqual([
      'license_forbids_redistribution',
      'source_unavailable',
    ])
  })

  it('derives only a KIND from `field` — pre-removal indices are never used to position', () => {
    // `notes[2]` addresses the slot the value had BEFORE quarantine re-indexed the list.
    expect(withheldFieldKind('notes[2]')).toBe('notes')
    expect(withheldFieldKind('names.el')).toBe('names')
    expect(withheldFieldKind('reviews')).toBe('reviews')
    // The index must not survive into anything renderable.
    const notice = createNeedsConnectivity({
      site_id: SITE_A,
      field: 'notes[2]',
      reason: 'source_unavailable',
    })
    expect(notice.dataset.withheldField).toBe('notes')
    expect(notice.textContent).not.toMatch(/\[2\]/)
    expect(notice.outerHTML).not.toMatch(/\[2\]/)
  })

  it('appends the block at the END of the card, not spliced among surviving values', async () => {
    const travel = await loaded()
    const site = travel.sites.get(SITE_A)
    if (!site) throw new Error('fixture site missing')
    const card = createPlaceCard({
      site,
      stories: [],
      withheld: travel.withheld.get(SITE_A) ?? [],
      lang: 'en',
    }) as HTMLElement
    expect(card.lastElementChild?.className).toBe('siyur-withheld-block')
    // No affordance sits inside a rendered value — that would read as a stamp on it.
    expect(card.querySelectorAll('.siyur-value .siyur-withheld')).toHaveLength(0)
  })

  it('de-duplicates by kind, because the indices that would distinguish them are unusable', () => {
    const block = createWithheldBlock([
      { site_id: SITE_A, field: 'notes[0]', reason: 'source_unavailable' },
      { site_id: SITE_A, field: 'notes[2]', reason: 'source_unavailable' },
      { site_id: SITE_A, field: 'reviews', reason: 'license_forbids_redistribution' },
    ]) as HTMLElement
    expect(block.querySelectorAll('.siyur-withheld')).toHaveLength(2)
  })

  it('renders nothing at all when nothing was withheld', () => {
    expect(createWithheldBlock([])).toBeNull()
  })
})

describe('bundle content parsing fails closed', () => {
  it('refuses an itinerary with no addressable stops rather than showing an empty day', () => {
    expect(parseItinerary({ ...ITINERARY, stops: [] })).toBeNull()
    expect(parseItinerary({ ...ITINERARY, schema_ver: 'ItineraryV2' })).toBeNull()
    expect(parseItinerary({ ...ITINERARY, date: '12/08/2026' })).toBeNull()
  })

  it('drops a timeline entry that addresses both a stop and a leg, or neither', () => {
    const parsed = parseItinerary({
      ...ITINERARY,
      timeline: {
        entries: [
          { stop_order: 0, leg_id: 'leg-0', start: '09:30', duration_min: 10 },
          { start: '09:40', duration_min: 10 },
          { stop_order: 1, start: '10:40', duration_min: 30 },
        ],
      },
    })
    expect(parsed?.timeline).toHaveLength(1)
    expect(parsed?.timeline[0]?.stop_order).toBe(1)
  })

  it('drops a leg with no source stamp — an uncredited leg is not showable', () => {
    const parsed = parseItinerary({
      ...ITINERARY,
      legs: [{ ...ITINERARY.legs[0], source: { kind: 'osm' } }],
    })
    expect(parsed?.legs).toHaveLength(0)
  })

  it('accepts both shapes the schema card leaves unpinned for sites and narrations', () => {
    // Documented in `travel/parse.ts`: the card fixes the paths and hashes but not the
    // internal JSON shape, and the compiler has not landed. When it pins one, the other
    // branch is deleted — this test is the record of which two were accepted.
    expect(parseSites(SITES).size).toBe(2)
    expect(parseSites({ sites: SITES }).size).toBe(2)
    expect(parseNarrations(NARRATIONS).get(SITE_B)).toHaveLength(1)
    expect(
      parseNarrations([{ site_id: SITE_B, stories: NARRATIONS[SITE_B] }]).get(SITE_B),
    ).toHaveLength(1)
  })

  it('drops a site whose location is unstamped (FR-003, unchanged from DU-03)', () => {
    const unstamped = [{ ...SITES[0], location: { value: SITES[0]?.location.value } }]
    expect(parseSites(unstamped).size).toBe(0)
  })
})

describe('off-route recovery is computed on-device (T052)', () => {
  const recovery = new OffRouteRecovery(parseWalkGraph(WALK_GRAPH))
  const corner: [number, number] = [28.2247, 36.4443]
  const far: [number, number] = [28.2267, 36.4453]

  it('routes over the bundled graph rather than cutting the corner', () => {
    const route = recovery.route(corner, far)
    expect(route.method).toBe('graph')
    // The graph is L-shaped: a real path has an interior vertex and is LONGER than the
    // straight line. Both halves matter — a fallback would satisfy neither.
    expect(route.coordinates.length).toBeGreaterThan(2)
    expect(route.distance_m).toBeGreaterThan(haversineMetres(corner, far))
  })

  it('falls back to a straight line, labelled as one, when the graph cannot help', () => {
    const offNetwork: [number, number] = [-58.4, -34.6]
    const route = recovery.route(corner, offNetwork)
    expect(route.method).toBe('straight_line')
    expect(route.coordinates).toEqual([corner, offNetwork])
  })

  it('returns a straight line rather than throwing when there is no graph at all', () => {
    const route = new OffRouteRecovery(null).route(corner, far)
    expect(route.method).toBe('straight_line')
    expect(route.distance_m).toBeGreaterThan(0)
  })

  it('reports distance in METRES, checked against the great-circle rule', () => {
    // Assert the external rule — one degree of latitude on a sphere of radius R is
    // πR/180 ≈ 111 195 m — not a literal this implementation emits. A kilometres/metres
    // mix-up (the `path.weight` trap) fails this by three orders of magnitude.
    const oneDegree = haversineMetres([0, 0], [0, 1])
    expect(oneDegree).toBeCloseTo((Math.PI * 6_371_008.8) / 180, 0)
    expect(oneDegree).toBeGreaterThan(111_000)
    expect(oneDegree).toBeLessThan(111_400)
  })

  it('refuses a walk graph that is not a LineString FeatureCollection', () => {
    expect(parseWalkGraph(null)).toBeNull()
    expect(parseWalkGraph({ type: 'FeatureCollection', features: [] })).toBeNull()
    expect(
      parseWalkGraph({
        type: 'FeatureCollection',
        features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] } }],
      }),
    ).toBeNull()
  })
})
