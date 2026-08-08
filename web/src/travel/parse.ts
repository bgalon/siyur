/**
 * Narrowing for the bundle's content artifacts.
 *
 * A bundle is a downloaded file. It is trusted exactly as much as a `fetch()` body —
 * which is to say not at all — so everything crossing from OPFS into a render goes
 * through here, the same discipline `../map/guards.ts` applies to the wire.
 *
 * ## Two shapes the schema card does not pin, named honestly
 *
 * `docs/data/bundle-manifest.md` fixes the *paths* `content.sites` and
 * `content.narrations` and fixes their hashes, but not the internal JSON shape of
 * either file. The compiler (T036–T040) is being written in a parallel workstream and
 * has not landed. Rather than guess once and be silently wrong, both readers accept the
 * two shapes the rest of the codebase makes plausible:
 *
 *   - `sites.json` — a bare `[SiteRecordV1]`, or `{ "sites": [...] }` (the `GET /sites`
 *     envelope this app already parses).
 *   - `narrations.json` — `{ "<site_id>": [Story] }`, or `[{ site_id, stories }]`
 *     (the row shape `withheld` uses).
 *
 * **This is the one place in the slice that is not derived from a card**, and it is
 * isolated to two functions on purpose: when the compiler pins the shape, one of the
 * two branches in each is deleted and nothing else moves.
 */

import { sanitiseSite } from '../map/guards'
import type { SiteRecordV1, SourceRef } from '../map/types'
import type { ItineraryV1, LonLat, RouteLegV1, Stop, Story, TimelineEntry } from './types'

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

const str = (v: unknown): string | null =>
  typeof v === 'string' && v.trim() !== '' ? v : null

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null

const int = (v: unknown): number | null => (num(v) === null ? null : Math.trunc(v as number))

/** A `SourceRef` needs BOTH halves — a half-stamp is not a stamp (`guards.ts`). */
function sourceRef(v: unknown): SourceRef | null {
  if (!isRecord(v)) return null
  const kind = str(v.kind)
  const license = str(v.license)
  if (!kind || !license) return null
  return {
    kind,
    license,
    id: str(v.id),
    url: str(v.url),
    attribution: str(v.attribution),
  }
}

/** `[lon, lat]` inside EPSG:4326 bounds. A swapped pair is refused, never corrected. */
function lonLat(v: unknown): LonLat | null {
  if (!Array.isArray(v) || v.length < 2) return null
  const lon = num(v[0])
  const lat = num(v[1])
  if (lon === null || lat === null) return null
  if (lon < -180 || lon > 180 || lat < -90 || lat > 90) return null
  return [lon, lat]
}

function lineString(v: unknown): { type: 'LineString'; coordinates: LonLat[] } | null {
  if (!isRecord(v) || v.type !== 'LineString' || !Array.isArray(v.coordinates)) return null
  const coordinates: LonLat[] = []
  for (const pair of v.coordinates) {
    const point = lonLat(pair)
    if (!point) return null
    coordinates.push(point)
  }
  return coordinates.length >= 2 ? { type: 'LineString', coordinates } : null
}

/** `HH:MM` local wall clock. Not parsed into a `Date` — see `Stop.planned_start`. */
function localTime(v: unknown): string | null {
  const s = str(v)
  return s && /^\d{2}:\d{2}(:\d{2})?$/.test(s) ? s : null
}

export function parseLeg(v: unknown): RouteLegV1 | null {
  if (!isRecord(v)) return null
  const id = str(v.id)
  const from_stop = int(v.from_stop)
  const to_stop = int(v.to_stop)
  const geometry = lineString(v.geometry)
  const distance_m = num(v.distance_m)
  const duration_s = int(v.duration_s)
  const source = sourceRef(v.source)
  // No stamp ⇒ not rendered, exactly as for a `SourcedValue` (FR-003). A leg with no
  // source cannot carry its ODbL credit, and an uncredited leg is not showable.
  if (
    !id ||
    from_stop === null ||
    to_stop === null ||
    !geometry ||
    distance_m === null ||
    duration_s === null ||
    !source
  ) {
    return null
  }
  return { id, from_stop, to_stop, mode: 'walk', geometry, distance_m, duration_s, source }
}

function parseStop(v: unknown): Stop | null {
  if (!isRecord(v)) return null
  const site_id = str(v.site_id)
  const order = int(v.order)
  const planned_start = localTime(v.planned_start)
  const dwell_min = int(v.dwell_min)
  if (!site_id || order === null || !planned_start || dwell_min === null) return null
  return { site_id, order, planned_start, dwell_min }
}

/**
 * A timeline entry addresses **exactly one** of a stop (by `order`) or a leg (by id).
 * An entry carrying both, or neither, is not addressable and is dropped rather than
 * guessed at — the itinerary card is explicit that there is one scheme, not two.
 */
function parseTimelineEntry(v: unknown): TimelineEntry | null {
  if (!isRecord(v)) return null
  const stop_order = int(v.stop_order)
  const leg_id = str(v.leg_id)
  const start = localTime(v.start)
  const duration_min = int(v.duration_min)
  if (start === null || duration_min === null) return null
  if ((stop_order === null) === (leg_id === null)) return null
  return { stop_order, leg_id, start, duration_min }
}

/**
 * Narrow the frozen `ItineraryV1`. Returns `null` when the day has no addressable
 * stops — an itinerary with nothing to visit is not a day, and rendering an empty
 * timeline would look like a successfully loaded blank plan (FR-020, fail closed).
 */
export function parseItinerary(v: unknown): ItineraryV1 | null {
  if (!isRecord(v) || v.schema_ver !== 'ItineraryV1') return null
  const id = str(v.id)
  const area_id = str(v.area_id)
  const date = str(v.date)
  if (!id || !area_id || !date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null

  const stops = (Array.isArray(v.stops) ? v.stops : [])
    .map(parseStop)
    .filter((s): s is Stop => s !== null)
    .sort((a, b) => a.order - b.order)
  if (stops.length === 0) return null

  const budgets = isRecord(v.budgets) ? v.budgets : {}
  return {
    id,
    area_id,
    date,
    lang: str(v.lang) ?? 'en',
    stops,
    legs: (Array.isArray(v.legs) ? v.legs : [])
      .map(parseLeg)
      .filter((l): l is RouteLegV1 => l !== null),
    timeline: (Array.isArray(isRecord(v.timeline) ? v.timeline.entries : v.timeline)
      ? ((isRecord(v.timeline) ? v.timeline.entries : v.timeline) as unknown[])
      : []
    )
      .map(parseTimelineEntry)
      .filter((e): e is TimelineEntry => e !== null),
    budgets: { walking_m: num(budgets.walking_m) ?? 0, hours: num(budgets.hours) ?? 0 },
    schema_ver: 'ItineraryV1',
  }
}

/** `routing/legs.json` — a bare array, or `{ legs: [...] }`. */
export function parseLegs(v: unknown): readonly RouteLegV1[] {
  const rows = Array.isArray(v) ? v : isRecord(v) && Array.isArray(v.legs) ? v.legs : []
  return rows.map(parseLeg).filter((l): l is RouteLegV1 => l !== null)
}

/** `content/sites.json` — see the module note on the two accepted shapes. */
export function parseSites(v: unknown): ReadonlyMap<string, SiteRecordV1> {
  const rows = Array.isArray(v) ? v : isRecord(v) && Array.isArray(v.sites) ? v.sites : []
  const out = new Map<string, SiteRecordV1>()
  for (const row of rows) {
    const site = sanitiseSite(row)
    if (site) out.set(site.id, site)
  }
  return out
}

function parseStory(v: unknown): Story | null {
  if (!isRecord(v)) return null
  const source = sourceRef(v.source)
  if (!source) return null
  const texts: Record<string, string> = {}
  if (isRecord(v.text_by_lang)) {
    for (const [lang, text] of Object.entries(v.text_by_lang)) {
      const value = str(text)
      if (str(lang) && value) texts[lang] = value
    }
  }
  if (Object.keys(texts).length === 0) return null
  return { text_by_lang: texts, source, ...(str(v.observed_at) ? { observed_at: str(v.observed_at) as string } : {}) }
}

/** `content/narrations.json` — see the module note on the two accepted shapes. */
export function parseNarrations(v: unknown): ReadonlyMap<string, readonly Story[]> {
  const out = new Map<string, readonly Story[]>()
  const add = (siteId: string | null, raw: unknown): void => {
    if (!siteId || !Array.isArray(raw)) return
    const stories = raw.map(parseStory).filter((s): s is Story => s !== null)
    if (stories.length > 0) out.set(siteId, stories)
  }
  if (Array.isArray(v)) {
    for (const row of v) if (isRecord(row)) add(str(row.site_id), row.stories)
  } else if (isRecord(v)) {
    for (const [siteId, stories] of Object.entries(v)) add(str(siteId), stories)
  }
  return out
}
