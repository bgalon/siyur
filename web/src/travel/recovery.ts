/**
 * T052 — off-route recovery, computed **on-device** over the bundled pruned graph.
 *
 * The bundle ships precomputed Valhalla legs for the planned day, and those carry the
 * experience. But a traveller who wanders off the plan needs a route Valhalla was never
 * asked for, and there is no maintained OSRM/Valhalla-wasm to run offline (stack
 * reference §5). So the bundle also ships a **topologically-noded GeoJSON line network**
 * (`routing.walk_graph`, EPSG:4326, ODbL) and `geojson-path-finder` (ISC) walks it here.
 *
 * **It is approximate by design** — no turn restrictions, naive costing — which
 * `route-leg.md` states outright. That is the accepted cost of routing with no server.
 *
 * **Straight line is the last resort, and it is labelled as one.** When the graph cannot
 * connect the two points (the traveller is off the pruned network, or the network is
 * disconnected there) the recovery returns a two-point line with
 * `method: 'straight_line'`. A caller must be able to tell that apart from a real route
 * — drawn identically, a straight line across a block of buildings is a confident,
 * wrong instruction. The distinction is in the return value, not in a comment.
 *
 * **The LLM computes none of this.** Geometry and distance come from the graph and from
 * haversine arithmetic here; a model emitting coordinates produces plausible, wrong
 * geometry (AGENTS.md geo rules).
 */

import PathFinder from 'geojson-path-finder'

import type { LonLat } from './types'

/**
 * `geojson-path-finder`'s own parameter types, derived rather than imported.
 *
 * Its `.d.ts` imports from `@types/geojson`, which pnpm's strict layout keeps out of
 * this package's `node_modules` — so `import type { Feature } from 'geojson'` does not
 * resolve here even though tsc reads the same types happily inside the library. Naming
 * them off the library's own signature keeps the binding exact without adding a
 * types-only dependency for two aliases, and it cannot drift from the version installed.
 */
type Finder = PathFinder<unknown, Record<string, unknown>>
type FinderNetwork = ConstructorParameters<typeof PathFinder<unknown, Record<string, unknown>>>[0]
type FinderPoint = Parameters<Finder['findPath']>[0]

/** A GeoJSON LineString feature of the pruned walking network. */
export interface WalkGraphFeature {
  readonly type: 'Feature'
  readonly geometry: { readonly type: 'LineString'; readonly coordinates: readonly LonLat[] }
  readonly properties: Readonly<Record<string, unknown>>
}

/** `routing/walk_graph.geojson` — a `FeatureCollection` of LineStrings, EPSG:4326. */
export interface WalkGraph {
  readonly type: 'FeatureCollection'
  readonly features: readonly WalkGraphFeature[]
}

export type RecoveryMethod = 'graph' | 'straight_line'

export interface RecoveryRoute {
  /** EPSG:4326 `[lon, lat]` vertices, origin first. */
  readonly coordinates: readonly LonLat[]
  readonly method: RecoveryMethod
  /** Metres — the unit `RouteLegV1.distance_m` uses, so the two are comparable. */
  readonly distance_m: number
}

const EARTH_RADIUS_M = 6_371_008.8

/**
 * Great-circle distance in metres between two `[lon, lat]` points.
 *
 * Haversine, not a planar approximation: a walking graph spans a few kilometres where
 * the difference is small, but a planar formula silently degrades with latitude and
 * this function is the only place the app converts geometry into a number a traveller
 * acts on.
 */
export function haversineMetres(a: LonLat, b: LonLat): number {
  const toRad = (deg: number): number => (deg * Math.PI) / 180
  const [lon1, lat1] = a
  const [lon2, lat2] = b
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)))
}

/** Summed leg-by-leg length of a polyline, in metres. */
export function pathLengthMetres(coordinates: readonly LonLat[]): number {
  let total = 0
  for (let i = 1; i < coordinates.length; i++) {
    const previous = coordinates[i - 1]
    const current = coordinates[i]
    if (previous && current) total += haversineMetres(previous, current)
  }
  return total
}

/** Narrow the bundled walk graph, dropping anything that is not a usable LineString. */
export function parseWalkGraph(raw: unknown): WalkGraph | null {
  if (typeof raw !== 'object' || raw === null) return null
  const value = raw as { type?: unknown; features?: unknown }
  if (value.type !== 'FeatureCollection' || !Array.isArray(value.features)) return null

  const features: WalkGraphFeature[] = []
  for (const feature of value.features) {
    const geometry = (feature as { geometry?: { type?: unknown; coordinates?: unknown } })?.geometry
    if (geometry?.type !== 'LineString' || !Array.isArray(geometry.coordinates)) continue
    const coordinates: LonLat[] = []
    for (const pair of geometry.coordinates) {
      if (!Array.isArray(pair)) continue
      const [lon, lat] = pair as unknown[]
      if (typeof lon !== 'number' || typeof lat !== 'number') continue
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue
      if (lon < -180 || lon > 180 || lat < -90 || lat > 90) continue
      coordinates.push([lon, lat])
    }
    // A one-vertex "line" contributes no edge and would make the topology builder
    // produce a vertex with no way out.
    if (coordinates.length >= 2) {
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates },
        properties: {},
      })
    }
  }
  return features.length > 0 ? { type: 'FeatureCollection', features } : null
}

/**
 * A GeoJSON `Point` feature for `findPath`.
 *
 * The coordinates are copied into a **mutable** array: `LonLat` is `readonly` here (the
 * app's own discipline, so a parsed geometry cannot be edited in place) while
 * `geojson`'s `Position` is mutable, and the library reserves the right to treat it so.
 */
function asPoint(coordinates: LonLat): FinderPoint {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [coordinates[0], coordinates[1]] },
    properties: {},
  }
}

/**
 * On-device recovery routing over one bundled walk graph.
 *
 * Built once and reused: `PathFinder`'s constructor does the topology + compaction pass
 * over the whole network, which is the expensive part. Rebuilding it per query would
 * turn a recovery into a multi-second stall on the device with the least CPU.
 */
export class OffRouteRecovery {
  private readonly finder: Finder | null

  constructor(graph: WalkGraph | null) {
    this.finder = graph ? new PathFinder(graph as unknown as FinderNetwork) : null
  }

  /**
   * Route from `from` to `to`, falling back to a straight line.
   *
   * Never throws and never returns `null`: a traveller who is lost gets a bearing even
   * when the graph cannot help, and the `method` field says which one they got.
   */
  route(from: LonLat, to: LonLat): RecoveryRoute {
    const straight: RecoveryRoute = {
      coordinates: [from, to],
      method: 'straight_line',
      distance_m: haversineMetres(from, to),
    }
    if (!this.finder) return straight

    let path: { path: number[][] } | undefined
    try {
      path = this.finder.findPath(asPoint(from), asPoint(to)) as { path: number[][] } | undefined
    } catch {
      // A point outside the network's tolerance makes the phantom-node insertion fail.
      // That is a normal off-network situation, not a crash worth surfacing.
      return straight
    }
    if (!path || path.path.length < 2) return straight

    const coordinates: LonLat[] = []
    for (const pair of path.path) {
      const lon = pair[0]
      const lat = pair[1]
      if (typeof lon === 'number' && typeof lat === 'number') coordinates.push([lon, lat])
    }
    if (coordinates.length < 2) return straight

    // Distance is recomputed from the returned geometry rather than read off
    // `path.weight`: the default weight is turf distance in **kilometres**, and a unit
    // mismatch here would show a 400 m detour as "0.4 m" — small enough to look like a
    // rounding bug rather than a wrong unit.
    return { coordinates, method: 'graph', distance_m: pathLengthMetres(coordinates) }
  }
}
