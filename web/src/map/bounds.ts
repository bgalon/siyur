/**
 * Geographic extent of a resolved area, so the map can show what was researched.
 *
 * Without this the map stays where it was — `createMap` opens at `center [0,0],
 * zoom 1` — and a researched old town is a fraction of one pixel. Every marker
 * renders correctly and the user sees an empty world, which reads as "research
 * found nothing" when in fact it found hundreds of places.
 *
 * EPSG:4326, **lon first** (`docs/data/area.md`). Latitude and longitude are both
 * plausible-looking numbers for many places, so a transposed pair cannot be caught
 * by range checks alone — `boundsOfPolygon` therefore validates each axis against
 * its own range and refuses the ring rather than returning a wrong extent.
 */

import type { GeoPolygon } from './types'

/** `[[west, south], [east, north]]` — the form MapLibre's `fitBounds` accepts. */
export type LngLatBoundsLike = readonly [readonly [number, number], readonly [number, number]]

const isLon = (n: number): boolean => Number.isFinite(n) && n >= -180 && n <= 180
const isLat = (n: number): boolean => Number.isFinite(n) && n >= -90 && n <= 90

/**
 * The bounding box of a GeoJSON Polygon, or `null` when the ring is unusable.
 *
 * `null` rather than a throw: a caller that cannot frame the area should leave the
 * view alone, not fail the research pass that already succeeded.
 */
export function boundsOfPolygon(polygon: GeoPolygon | null | undefined): LngLatBoundsLike | null {
  const ring = polygon?.coordinates?.[0]
  if (!ring || ring.length === 0) return null

  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity

  for (const position of ring) {
    const lon = position?.[0]
    const lat = position?.[1]
    // A single bad vertex invalidates the extent: a silently-skipped one would
    // frame the wrong area, which is worse than not moving the map at all.
    if (typeof lon !== 'number' || typeof lat !== 'number') return null
    if (!isLon(lon) || !isLat(lat)) return null
    if (lon < west) west = lon
    if (lon > east) east = lon
    if (lat < south) south = lat
    if (lat > north) north = lat
  }

  if (west > east || south > north) return null
  return [
    [west, south],
    [east, north],
  ]
}
