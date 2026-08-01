/**
 * The provenance gate (FR-003 / SC-002 / Constitution Article V).
 *
 * `GET /sites` promises never to return an unstamped value, but the client does
 * not take that on trust: **every** value crossing from the wire into the DOM is
 * narrowed here first. A value that fails {@link isStamped} has no source, and a
 * value with no source is never rendered — there is no other code path from a
 * response to a marker.
 */

import type {
  AreaCoverage,
  AreaResolution,
  GeoPoint,
  GeoPolygon,
  ResearchStatus,
  ResearchSummary,
  SiteRecordV1,
  SitesResponse,
  SourceRef,
  SourcedValue,
} from './types'

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function isNonEmptyString(v: unknown): v is string {
  return typeof v === 'string' && v.trim().length > 0
}

/**
 * A `SourceRef` is only usable if it names BOTH where the value came from
 * (`kind`) and under what terms (`license`). A half-stamp is not a stamp: the
 * client would have to invent the missing half, which it must never do.
 */
export function isSourceRef(v: unknown): v is SourceRef {
  return isRecord(v) && isNonEmptyString(v.kind) && isNonEmptyString(v.license)
}

/**
 * Narrow an unknown wire value to a `SourcedValue<T>`. `valueGuard` checks the
 * payload; the stamp check is non-negotiable and applies to every field.
 */
export function isStamped<T>(
  v: unknown,
  valueGuard: (x: unknown) => x is T,
): v is SourcedValue<T> {
  return isRecord(v) && 'value' in v && valueGuard(v.value) && isSourceRef(v.source)
}

/** Convenience guard for the common `SourcedValue<string>` case. */
export function isStampedString(v: unknown): v is SourcedValue<string> {
  return isStamped(v, isNonEmptyString)
}

/** EPSG:4326 `Point` with a plausible `[lon, lat]` pair (FR-005). */
export function isGeoPoint(v: unknown): v is GeoPoint {
  if (!isRecord(v) || v.type !== 'Point' || !Array.isArray(v.coordinates)) return false
  const [lon, lat] = v.coordinates as unknown[]
  return (
    typeof lon === 'number' &&
    typeof lat === 'number' &&
    Number.isFinite(lon) &&
    Number.isFinite(lat) &&
    lon >= -180 &&
    lon <= 180 &&
    lat >= -90 &&
    lat <= 90
  )
}

/** `SourcedValue<GeoPoint>` — a location we may place a marker at. */
export function isStampedLocation(v: unknown): v is SourcedValue<GeoPoint> {
  return isStamped(v, isGeoPoint)
}

/**
 * Sanitise one wire record: drop every unstamped value, keep the rest.
 * Returns `null` when the record cannot be rendered at all — no id, or no
 * stamped location to pin a marker to.
 */
export function sanitiseSite(raw: unknown): SiteRecordV1 | null {
  if (!isRecord(raw)) return null
  if (!isNonEmptyString(raw.id)) return null
  if (!isStampedLocation(raw.location)) return null

  const names: Record<string, SourcedValue<string>> = {}
  if (isRecord(raw.names)) {
    for (const [tag, value] of Object.entries(raw.names)) {
      if (isNonEmptyString(tag) && isStampedString(value)) names[tag] = value
    }
  }

  const categories = Array.isArray(raw.categories)
    ? raw.categories.filter(isStampedString)
    : []

  return {
    id: raw.id,
    gers_id: isNonEmptyString(raw.gers_id) ? raw.gers_id : null,
    ...(isNonEmptyString(raw.schema_ver) ? { schema_ver: raw.schema_ver } : {}),
    names,
    location: raw.location,
    categories,
    address: isStampedString(raw.address) ? raw.address : null,
    opening_hours: isStampedString(raw.opening_hours) ? raw.opening_hours : null,
    conflicts: Array.isArray(raw.conflicts) ? raw.conflicts : [],
    ...(isNonEmptyString(raw.updated_at) ? { updated_at: raw.updated_at } : {}),
  }
}

/**
 * Sanitise a whole `GET /sites` body. Unrenderable sites are dropped rather
 * than rendered half-stamped; `attribution[]` is reduced to non-empty strings
 * (the server owns the exact wording — see `attribution.ts`).
 */
export function sanitiseSitesResponse(raw: unknown): SitesResponse {
  const body = isRecord(raw) ? raw : {}
  const sites = Array.isArray(body.sites)
    ? body.sites.map(sanitiseSite).filter((s): s is SiteRecordV1 => s !== null)
    : []
  const attribution = Array.isArray(body.attribution)
    ? body.attribution.filter(isNonEmptyString)
    : []
  return { sites, attribution }
}

/* ------------------------------------------------- POST /areas (US2) ------- */

/** A closed EPSG:4326 ring of `[lon, lat]` pairs, or `null` if any pair is bad. */
function sanitiseRing(raw: unknown): [number, number][] | null {
  if (!Array.isArray(raw) || raw.length < 4) return null
  const ring: [number, number][] = []
  for (const pair of raw) {
    if (!Array.isArray(pair)) return null
    const [lon, lat] = pair as unknown[]
    if (!isGeoPoint({ type: 'Point', coordinates: [lon, lat] })) return null
    ring.push([lon as number, lat as number])
  }
  return ring
}

/** The resolved polygon, or `null` — the client never repairs a bad geometry. */
export function sanitisePolygon(raw: unknown): GeoPolygon | null {
  if (!isRecord(raw) || raw.type !== 'Polygon' || !Array.isArray(raw.coordinates)) return null
  const rings: [number, number][][] = []
  for (const ring of raw.coordinates) {
    const clean = sanitiseRing(ring)
    if (!clean) return null
    rings.push(clean)
  }
  return rings.length > 0 ? { type: 'Polygon', coordinates: rings } : null
}

/**
 * Narrow the wire `coverage` block.
 *
 * `covered` is taken **strictly** from the server's own boolean — the client
 * does not infer coverage from the count. A malformed block therefore reads as
 * *not* covered, which is safe in both directions: nothing here ever starts a
 * research pass, so the worst case is a "start researching" CTA over an area the
 * server will answer with a reuse hint (`force_refresh=false`, T050).
 *
 * `refresh_available` is the one field that may be raised rather than read: the
 * contract states it is *always* true when covered and FR-006 requires the
 * refresh option to be offered, so a covered area always gets one.
 */
export function sanitiseCoverage(raw: unknown): AreaCoverage {
  const body = isRecord(raw) ? raw : {}
  const count = body.known_site_count
  const covered = body.covered === true
  return {
    known_site_count:
      typeof count === 'number' && Number.isFinite(count) && count >= 0 ? Math.trunc(count) : 0,
    covered,
    stalest_observed_at: isNonEmptyString(body.stalest_observed_at)
      ? body.stalest_observed_at
      : null,
    refresh_available: body.refresh_available === true || covered,
  }
}

/**
 * Sanitise a whole `POST /areas` body. Returns `null` without an `area_id` —
 * there is nothing to reuse or refresh without one.
 */
export function sanitiseAreaResolution(raw: unknown): AreaResolution | null {
  if (!isRecord(raw) || !isNonEmptyString(raw.area_id)) return null
  return {
    area_id: raw.area_id,
    polygon: sanitisePolygon(raw.polygon),
    coverage: sanitiseCoverage(raw.coverage),
  }
}

/* --------------------------------- research SSE frames (US1 / FR-012) ------ */

/** A non-negative integer count on the wire. */
function isCount(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0
}

const countOrZero = (v: unknown): number => (isCount(v) ? Math.trunc(v) : 0)

/**
 * Narrow a `status` frame. Returns `null` without a `phase` — a progress line
 * that cannot name its phase is not progress the user can read, and the client
 * will not label it "working…" on the server's behalf.
 */
export function sanitiseResearchStatus(raw: unknown): ResearchStatus | null {
  if (!isRecord(raw) || !isNonEmptyString(raw.phase)) return null
  return {
    phase: raw.phase,
    msg: isNonEmptyString(raw.msg) ? raw.msg : null,
    source: isNonEmptyString(raw.source) ? raw.source : null,
    found: isCount(raw.found) ? Math.trunc(raw.found) : null,
    degraded: raw.degraded === true,
  }
}

/**
 * Narrow the `summary` frame.
 *
 * `readable` is keyed to `sites`, the one field the contract always sends: an
 * unparseable frame therefore yields zeroed totals **flagged as meaningless**,
 * so nothing downstream can report "0 sites found" as a finding when the truth is
 * "we never learned the total" (FR-012).
 */
export function sanitiseResearchSummary(raw: unknown): ResearchSummary {
  const body = isRecord(raw) ? raw : {}

  const sources: Record<string, number> = {}
  if (isRecord(body.sources)) {
    for (const [name, count] of Object.entries(body.sources)) {
      if (isNonEmptyString(name) && isCount(count)) sources[name] = Math.trunc(count)
    }
  }

  return {
    sites: countOrZero(body.sites),
    new: countOrZero(body.new),
    reused: countOrZero(body.reused),
    conflicts: countOrZero(body.conflicts),
    sources,
    degraded_sources: Array.isArray(body.degraded_sources)
      ? body.degraded_sources.filter(isNonEmptyString)
      : [],
    readable: isCount(body.sites),
  }
}

/** The `area_id` a `done` frame closes over, or `null` if it carried none. */
export function sanitiseResearchDone(raw: unknown): string | null {
  return isRecord(raw) && isNonEmptyString(raw.area_id) ? raw.area_id : null
}
