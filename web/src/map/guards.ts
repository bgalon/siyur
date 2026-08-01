/**
 * The provenance gate (FR-003 / SC-002 / Constitution Article V).
 *
 * `GET /sites` promises never to return an unstamped value, but the client does
 * not take that on trust: **every** value crossing from the wire into the DOM is
 * narrowed here first. A value that fails {@link isStamped} has no source, and a
 * value with no source is never rendered — there is no other code path from a
 * response to a marker.
 */

import type { GeoPoint, SiteRecordV1, SitesResponse, SourceRef, SourcedValue } from './types'

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
