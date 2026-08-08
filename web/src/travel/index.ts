/**
 * The travel surface's public API, and the one function that turns a verified bundle
 * into the day the traveller sees.
 *
 * **This module is never imported from `main.ts` statically.** It pulls in
 * `geojson-path-finder` and the whole travel renderer, none of which the planning
 * phase needs; the same dynamic-import discipline `main.ts` already applies to
 * `./map/basemap` keeps it out of the app-shell chunk (ADR-0003 footgun 1's sibling —
 * the shell is precached, so everything in it is downloaded by everyone).
 */

import { readJson, readText, type BundleStorage } from '../bundle/storage'
import type { BundleManifestV1, WithheldEntry } from '../bundle/types'
import type { SiteRecordV1 } from '../map/types'
import { parseItinerary, parseLegs, parseNarrations, parseSites } from './parse'
import { OffRouteRecovery, parseWalkGraph } from './recovery'
import { withheldBySite } from './withheld'
import type { ItineraryV1, RouteLegV1, Story } from './types'

export { parseItinerary, parseLeg, parseLegs, parseNarrations, parseSites } from './parse'
export {
  OffRouteRecovery,
  haversineMetres,
  parseWalkGraph,
  pathLengthMetres,
  type RecoveryMethod,
  type RecoveryRoute,
  type WalkGraph,
  type WalkGraphFeature,
} from './recovery'
export {
  createBundleCredit,
  createPlaceCard,
  createPlanCredit,
  renderDay,
  renderLeg,
  renderStory,
  type DayRenderOptions,
  type PlaceCardOptions,
} from './render'
export {
  createNeedsConnectivity,
  createWithheldBlock,
  withheldBySite,
  withheldFieldKind,
} from './withheld'
export type * from './types'

/** Everything the travel UI renders from, resolved out of one verified bundle. */
export interface TravelBundle {
  readonly itinerary: ItineraryV1
  readonly sites: ReadonlyMap<string, SiteRecordV1>
  readonly narrations: ReadonlyMap<string, readonly Story[]>
  readonly withheld: ReadonlyMap<string, readonly WithheldEntry[]>
  readonly legs: readonly RouteLegV1[]
  readonly recovery: OffRouteRecovery
  /** `ATTRIBUTION.md`, verbatim — the bundle's aggregate credit. */
  readonly attribution: string
}

/**
 * Load a bundle's content into the travel model.
 *
 * Returns `null` when the frozen itinerary cannot be read — fail closed. Everything
 * else degrades to empty (no narration, no recovery graph) because those are states a
 * *correct* bundle can legitimately be in: `poi-site.md` is explicit that a place with
 * no openly-licensed article carries no story and that this is not a defect, and the
 * manifest's own `textLicense` is `null` exactly then.
 *
 * The caller must have run {@link ../bundle/open!openBundle} first. This function does
 * no hashing: separating "are these bytes what the manifest says" from "what do these
 * bytes mean" is what lets the integrity failure be reported as one thing.
 */
export async function loadTravelBundle(
  storage: BundleStorage,
  manifest: BundleManifestV1,
): Promise<TravelBundle | null> {
  const itinerary = parseItinerary(await readJson(storage, manifest.content.itinerary))
  if (!itinerary) return null

  const [sitesRaw, narrationsRaw, legsRaw, graphRaw, attribution] = await Promise.all([
    readJson(storage, manifest.content.sites),
    readJson(storage, manifest.content.narrations),
    readJson(storage, manifest.routing.legs),
    readJson(storage, manifest.routing.walk_graph),
    readText(storage, manifest.attribution.path),
  ])

  return {
    itinerary,
    sites: parseSites(sitesRaw),
    narrations: parseNarrations(narrationsRaw),
    withheld: withheldBySite(manifest.withheld),
    // The itinerary's own embedded legs win when it has them; `routing/legs.json` is the
    // standalone freeze of the same objects and covers the M2 B/C branches that never
    // appear inside the base plan.
    legs: itinerary.legs.length > 0 ? itinerary.legs : parseLegs(legsRaw),
    recovery: new OffRouteRecovery(parseWalkGraph(graphRaw)),
    attribution: attribution ?? '',
  }
}
