/**
 * Wire types for `BundleManifestV1` — the airplane-mode contract.
 *
 * Field-level ground truth is `docs/data/bundle-manifest.md`; every type below is
 * derived from that card, not from what the client happens to need. Two properties
 * of the card are load-bearing here and are easy to lose in translation:
 *
 *  1. **`tiles.pmtiles` is a whole `TileSourceV1`**, not a four-field summary of one.
 *     The card says so explicitly because summarising it is the obvious shortcut.
 *  2. **`withheld[].field` indices are pre-removal.** They address the slot a value
 *     occupied *before* the quarantine filter ran, so surviving list entries have
 *     re-indexed and `notes[2]` no longer names the same slot — or any slot. The
 *     travel UI may use `field` to say *that* something was withheld and of what
 *     kind; it must never use it to position a placeholder inside the surviving list.
 *     {@link ../travel/withheld} is where that rule is enforced.
 *
 * Everything crossing from an OPFS file into these types goes through
 * {@link ./manifest!parseManifest} first — a bundle is a downloaded artifact, and
 * the client trusts it exactly as much as it trusts a `fetch()` body: not at all.
 */

/** EPSG:4326 `[west, south, east, north]`. */
export type Bbox4326 = readonly [number, number, number, number]

/** One hashed artifact: a manifest-relative path and the SHA-256 of its raw bytes. */
export interface HashedArtifact {
  readonly path: string
  readonly sha256: string
}

/**
 * `TileSourceV1`, embedded whole in `tiles.pmtiles` (`docs/data/tile-source.md`).
 *
 * Only the subset the card marks required here is modelled as non-optional —
 * `path`, `sha256`, `bbox`, `maxzoom`. The rest is carried through because the
 * client must be able to *render* the tile licence and attribution without
 * inventing them (ADR-0019: the stamp travels with the value).
 */
export interface TileSourceV1 {
  readonly path: string
  readonly sha256: string
  readonly bbox: Bbox4326
  readonly maxzoom: number
  readonly minzoom?: number
  readonly format?: string
  readonly schema_ver?: string
  readonly build_source?: string
  readonly build_date?: string
  /** SPDX id — `ODbL-1.0` for OSM-derived tiles. */
  readonly tile_license?: string
  /** The exact credit string to render. Never reconstructed from `tile_license`. */
  readonly attribution?: string
  readonly style?: HashedArtifact
  readonly glyphs?: { readonly path: string; readonly license?: string }
}

/** `routing` — the pruned walk graph and the precomputed legs, each hashed alone. */
export interface BundleRouting {
  readonly walk_graph: string
  readonly walk_graph_sha256: string
  readonly legs: string
  readonly legs_sha256: string
}

/** `content` — post-quarantine sites, narrations and the frozen itinerary. */
export interface BundleContent {
  readonly sites: string
  readonly sites_sha256: string
  readonly narrations: string
  readonly narrations_sha256: string
  readonly itinerary: string
  readonly itinerary_sha256: string
}

/**
 * `withheld[].reason` is a **closed enum** — free text is not permitted (ADR-0025 A3).
 *
 * There is deliberately no `"unstamped"` member: unstamped input is *refused* at
 * compile (FR-012), never withheld and shipped as a placeholder. A value is withheld
 * or refused, never both.
 */
export const WITHHELD_REASONS = [
  'license_forbids_redistribution',
  'source_unavailable',
] as const

export type WithheldReason = (typeof WITHHELD_REASONS)[number]

/** One value the quarantine filter removed, and why. */
export interface WithheldEntry {
  readonly site_id: string
  /** Dotted path — `reviews`, `notes[2]`, `names.el`. **Pre-removal indices.** */
  readonly field: string
  readonly reason: WithheldReason
}

/** `BundleManifestV1`. `schematic` is M2+ and absent from every M1 bundle. */
export interface BundleManifestV1 {
  readonly bundle_id: string
  readonly itinerary_id: string
  readonly created_at: string
  readonly size_bytes: number
  readonly schema_ver: 'BundleManifestV1'
  readonly tiles: { readonly pmtiles: TileSourceV1 }
  readonly routing: BundleRouting
  readonly content: BundleContent
  readonly attribution: HashedArtifact
  /**
   * The licence of the **bundled narration text** — `"CC-BY-SA-4.0"` whenever any
   * story is present, `null` when `content.narrations` is empty (ADR-0024).
   * Machine-readable on purpose: a reuser must not have to parse `ATTRIBUTION.md`
   * prose to learn the share-alike terms.
   */
  readonly textLicense: string | null
  readonly withheld: readonly WithheldEntry[]
  readonly integrity: { readonly manifest_sha256: string }
}

/**
 * Everything a manifest names, as one list of `(path, sha256)` pairs.
 *
 * The card's integrity discipline is **one hash per artifact, no shared hashes** —
 * "a hash that spans two files cannot say *which* one corrupted". This function is
 * the single place that knows the mapping, so a launch-time check cannot silently
 * skip an artifact by forgetting to name it.
 */
export function manifestArtifacts(manifest: BundleManifestV1): readonly HashedArtifact[] {
  return [
    { path: manifest.tiles.pmtiles.path, sha256: manifest.tiles.pmtiles.sha256 },
    { path: manifest.routing.walk_graph, sha256: manifest.routing.walk_graph_sha256 },
    { path: manifest.routing.legs, sha256: manifest.routing.legs_sha256 },
    { path: manifest.content.sites, sha256: manifest.content.sites_sha256 },
    { path: manifest.content.narrations, sha256: manifest.content.narrations_sha256 },
    { path: manifest.content.itinerary, sha256: manifest.content.itinerary_sha256 },
    { path: manifest.attribution.path, sha256: manifest.attribution.sha256 },
  ]
}
