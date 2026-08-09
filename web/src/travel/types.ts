/**
 * Types the travel UI renders from — all of them read out of the bundle, none of them
 * fetched. Ground truth: `docs/data/itinerary.md`, `docs/data/route-leg.md`,
 * `docs/data/poi-site.md`.
 *
 * `SourceRef` / `SourcedValue` / `GeoPoint` are imported from `../map/types` rather
 * than restated. They are the same wire types they have always been — ADR-0002's point
 * is that the *read model does not change* when the transport does, and duplicating the
 * spine here would be the first crack in that.
 */

import type { GeoPoint, SourceRef, SourcedValue } from '../map/types'

export type { GeoPoint, SourceRef, SourcedValue }

/** EPSG:4326 `[lon, lat]`. Never `[lat, lon]` — see the CRS note in every schema card. */
export type LonLat = readonly [number, number]

/** A `Stop` — `ItineraryV1.stops[]`. A stop has no id of its own; `order` addresses it. */
export interface Stop {
  readonly site_id: string
  readonly order: number
  /**
   * **Area-local wall clock (`HH:MM`), frozen as planned.** Not an instant: the
   * complete instant is this plus `ItineraryV1.date` in the area's timezone. The
   * traveller's device clock is explicitly not required to match, so this string is
   * rendered verbatim and never passed through `Date` — converting it would silently
   * shift the whole day when the device is on the wrong zone, which is the normal
   * state of a device that just got off a plane.
   */
  readonly planned_start: string
  readonly dwell_min: number
}

/**
 * A `Timeline.entries[]` row. **Exactly one** of `stop_order` / `leg_id` is present —
 * the plan addresses stops by position and legs by id, never both (itinerary card).
 */
export interface TimelineEntry {
  readonly stop_order: number | null
  readonly leg_id: string | null
  readonly start: string
  readonly duration_min: number
}

/** `RouteLegV1` — a precomputed walking leg. Geometry is EPSG:4326 LineString. */
export interface RouteLegV1 {
  readonly id: string
  readonly from_stop: number
  readonly to_stop: number
  readonly mode: 'walk'
  readonly geometry: { readonly type: 'LineString'; readonly coordinates: readonly LonLat[] }
  readonly distance_m: number
  readonly duration_s: number
  /**
   * A **bare** `SourceRef`, not a `SourcedValue` — a leg has no `bundleable` field to
   * read (route-leg card). Its bundleability is *derived* at compile; by the time it is
   * in the bundle the question is settled. What the client needs it for is the stamp:
   * `kind="osm"`, `id="valhalla:pedestrian"`, `license="ODbL-1.0"`.
   */
  readonly source: SourceRef
}

/** `ItineraryV1`, as frozen into `content.itinerary`. */
export interface ItineraryV1 {
  readonly id: string
  readonly area_id: string
  /** `YYYY-MM-DD` in the area's local frame, no offset. */
  readonly date: string
  readonly lang: string
  readonly stops: readonly Stop[]
  readonly legs: readonly RouteLegV1[]
  readonly timeline: readonly TimelineEntry[]
  readonly budgets: { readonly walking_m: number; readonly hours: number }
  readonly schema_ver: 'ItineraryV1'
}

/**
 * A `Story` — one adapted CC-BY-SA article, per `poi-site.md`.
 *
 * Like `RouteLegV1` it carries a **bare `SourceRef`** and no `bundleable` stamp. A story
 * whose `source` is missing is refused, not rendered: the whole point of the narration
 * posture (PRD §7, ADR-0024) is per-article attribution, and an unattributed story is
 * the one thing the licence does not permit us to show.
 */
export interface Story {
  readonly text_by_lang: Readonly<Record<string, string>>
  readonly source: SourceRef
  readonly observed_at?: string
}
