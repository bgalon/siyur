/**
 * The **synthetic bundle** — a whole `BundleManifestV1` and every artifact it names,
 * built in memory with real SHA-256 hashes and a real RFC 8785 manifest digest.
 *
 * The compiler (T036–T040) does not exist yet, so this fixture is what DU-06 is built
 * against. Every field below is derived from **`docs/data/bundle-manifest.md`**, not
 * from what the client happens to find convenient — if the runtime needs changing when
 * the real compiler lands, this fixture was wrong. In particular:
 *
 *  - `tiles.pmtiles` is a **whole `TileSourceV1`**, not a four-field summary (the card
 *    calls that out explicitly, because summarising it is the obvious shortcut).
 *  - `content` has **three** artifacts — sites, narrations *and* the frozen itinerary —
 *    each hashed on its own. One hash per artifact, no group hashes.
 *  - `withheld` uses the **closed** reason enum and a **pre-removal** dotted index
 *    (`notes[2]`), which is the case the travel UI must not use for positioning.
 *  - `textLicense` is `"CC-BY-SA-4.0"` because narration is present (ADR-0024).
 *  - `integrity.manifest_sha256` is computed by the same RFC 8785 path the runtime
 *    verifies with, over the manifest with `integrity` **removed**.
 *
 * Nothing here is bound to a place: Rhodes coordinates appear because Rhodes is the
 * demo fixture everywhere else in this repo, and every one of them is a parameter.
 */

import canonicalize from 'canonicalize'

import type { BundleStorage } from '../../src/bundle/storage'
import type { BundleManifestV1 } from '../../src/bundle/types'
import { buildPmtiles } from './pmtiles'

/** In-memory {@link BundleStorage} — jsdom has no OPFS. */
export class MemoryBundleStorage implements BundleStorage {
  readonly files = new Map<string, Uint8Array>()

  constructor(readonly key = 'memory://bundles/bnd_test') {}

  async size(path: string): Promise<number> {
    return this.files.get(path)?.byteLength ?? 0
  }

  async writeAt(path: string, offset: number, bytes: Uint8Array): Promise<void> {
    const existing = this.files.get(path) ?? new Uint8Array(0)
    const end = Math.max(existing.byteLength, offset + bytes.byteLength)
    const next = new Uint8Array(end)
    next.set(existing, 0)
    next.set(bytes, offset)
    this.files.set(path, next)
  }

  async read(path: string): Promise<Uint8Array | null> {
    return this.files.get(path) ?? null
  }

  async readRange(path: string, offset: number, length: number): Promise<Uint8Array | null> {
    const file = this.files.get(path)
    return file ? file.slice(offset, offset + length) : null
  }

  async remove(path: string): Promise<void> {
    this.files.delete(path)
  }
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

const utf8 = (text: string): Uint8Array => new TextEncoder().encode(text)
const jsonBytes = (value: unknown): Uint8Array => utf8(JSON.stringify(value, null, 2))

/** Stamp helper — every bundled value carries source + licence (Article V). */
const stamp = (kind: string, license: string, attribution?: string) => ({
  kind,
  id: `${kind}:fixture`,
  url: null,
  license,
  ...(attribution ? { attribution } : { attribution: null }),
})

const OSM = stamp('osm', 'ODbL-1.0', '© OpenStreetMap contributors')
const OVERTURE = stamp('overture', 'CDLA-Permissive-2.0')
const WIKIVOYAGE = stamp(
  'wikivoyage',
  'CC-BY-SA-4.0',
  '"Rhodes", Wikivoyage, https://en.wikivoyage.org/wiki/Rhodes — authors via page history',
)

export const SITE_A = '6f1c0000-0000-4000-8000-000000000001'
export const SITE_B = 'c9d10000-0000-4000-8000-000000000002'

const sourced = <T>(value: T, source: ReturnType<typeof stamp>) => ({
  value,
  source,
  bundleable: true,
  confidence: 0.9,
  observed_at: '2026-07-25',
})

/** Post-quarantine `content/sites.json`. `reviews` is absent — it was withheld. */
export const SITES = [
  {
    id: SITE_A,
    schema_ver: 'SiteRecordV1',
    names: { en: sourced('Palace of the Grand Master', OVERTURE) },
    location: sourced({ type: 'Point', coordinates: [28.2247, 36.4443] }, OVERTURE),
    categories: [sourced('museum', OVERTURE)],
    address: sourced('Ippoton, Rhodes', OSM),
    opening_hours: sourced('Mo-Su 08:00-20:00', OSM),
    conflicts: [],
  },
  {
    id: SITE_B,
    schema_ver: 'SiteRecordV1',
    names: { en: sourced('Street of the Knights', OVERTURE) },
    location: sourced({ type: 'Point', coordinates: [28.2238, 36.4447] }, OVERTURE),
    categories: [],
    conflicts: [],
  },
]

/** `content/narrations.json`. A `Story` carries a **bare** `SourceRef`, no stamp. */
export const NARRATIONS = {
  [SITE_B]: [
    {
      text_by_lang: { en: 'The cobbled street once housed the knightly tongues of the Order.' },
      source: WIKIVOYAGE,
      observed_at: '2026-07-25',
    },
  ],
}

const LEG = {
  id: 'leg-0',
  schema_ver: 'RouteLegV1',
  mode: 'walk',
  from_stop: 0,
  to_stop: 1,
  distance_m: 380,
  duration_s: 300,
  geometry: {
    type: 'LineString',
    coordinates: [
      [28.2247, 36.4443],
      [28.2242, 36.4446],
      [28.2238, 36.4447],
    ],
  },
  source: { kind: 'osm', id: 'valhalla:pedestrian', url: null, license: 'ODbL-1.0', attribution: '© OpenStreetMap contributors' },
}

/** `content/itinerary.json` — the frozen `ItineraryV1` the timeline renders from. */
export const ITINERARY = {
  id: '7be20000-0000-4000-8000-00000000000a',
  user_id: 'user-1',
  area_id: 'a0000000-0000-4000-8000-00000000000b',
  date: '2026-08-12',
  lang: 'en',
  schema_ver: 'ItineraryV1',
  stops: [
    { site_id: SITE_A, order: 0, planned_start: '09:30', dwell_min: 60 },
    { site_id: SITE_B, order: 1, planned_start: '10:40', dwell_min: 30 },
  ],
  legs: [LEG],
  timeline: {
    entries: [
      { stop_order: 0, start: '09:30', duration_min: 60 },
      { leg_id: 'leg-0', start: '10:30', duration_min: 5 },
      { stop_order: 1, start: '10:40', duration_min: 30 },
    ],
  },
  budgets: { walking_m: 4000, hours: 8 },
}

/**
 * `routing/walk_graph.geojson` — a pruned, topologically-noded network.
 *
 * Deliberately **L-shaped**: the direct line from the first vertex to the last cuts the
 * corner, so a recovery that actually walked the graph produces a longer path with an
 * interior vertex, and one that quietly fell back to a straight line does not. A
 * straight-line network would make the two indistinguishable and the test worthless.
 */
export const WALK_GRAPH = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'LineString',
        coordinates: [
          [28.2247, 36.4443],
          [28.2247, 36.4453],
        ],
      },
    },
    {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'LineString',
        coordinates: [
          [28.2247, 36.4453],
          [28.2267, 36.4453],
        ],
      },
    },
  ],
}

export const ATTRIBUTION_MD = [
  '# Sources and licences',
  '',
  'Map data © OpenStreetMap contributors, ODbL 1.0.',
  '',
  '"Rhodes", Wikivoyage — CC BY-SA 4.0, authors via page history.',
  '',
].join('\n')

export interface SyntheticBundle {
  readonly storage: MemoryBundleStorage
  readonly manifest: BundleManifestV1
  /** The manifest as it was written — the raw object the digest was taken over. */
  readonly raw: Record<string, unknown>
  /** The tile body stored at z0/x0/y0 in the archive. */
  readonly tile: Uint8Array
}

/**
 * Write a complete synthetic bundle into a fresh {@link MemoryBundleStorage}.
 *
 * `mutate` runs on the manifest **before** the digest is computed, so a test can build
 * a bundle that is internally consistent but describes something different (a different
 * `withheld` list, a missing narration) without hand-maintaining hashes.
 */
export async function buildSyntheticBundle(
  mutate?: (manifest: Record<string, unknown>) => void,
): Promise<SyntheticBundle> {
  const storage = new MemoryBundleStorage()
  const archive = buildPmtiles()

  const artifacts: Record<string, Uint8Array> = {
    'tiles/area.pmtiles': archive.bytes,
    'routing/walk_graph.geojson': jsonBytes(WALK_GRAPH),
    'routing/legs.json': jsonBytes([LEG]),
    'content/sites.json': jsonBytes(SITES),
    'content/narrations.json': jsonBytes(NARRATIONS),
    'content/itinerary.json': jsonBytes(ITINERARY),
    'ATTRIBUTION.md': utf8(ATTRIBUTION_MD),
  }
  for (const [path, bytes] of Object.entries(artifacts)) await storage.writeAt(path, 0, bytes)

  const hash = async (path: string): Promise<string> =>
    sha256(artifacts[path] as Uint8Array)

  const raw: Record<string, unknown> = {
    bundle_id: 'bnd_01JTEST',
    itinerary_id: ITINERARY.id,
    created_at: '2026-08-07T12:30:00Z',
    size_bytes: Object.values(artifacts).reduce((n, b) => n + b.byteLength, 0),
    schema_ver: 'BundleManifestV1',
    tiles: {
      pmtiles: {
        path: 'tiles/area.pmtiles',
        format: 'pmtiles',
        schema_ver: 'TileSourceV1',
        sha256: await hash('tiles/area.pmtiles'),
        // A whole-degree bound on purpose: this is the intermittent number-formatting
        // divergence the card warns about (`28.0` in Python, `28` in JavaScript), so
        // the fixture exercises it on every run rather than on the unlucky area.
        bbox: [28.0, 36.44, 28.232, 36.451],
        minzoom: 0,
        maxzoom: 15,
        build_source: 'protomaps-daily',
        build_date: '2026-08-06',
        tile_license: 'ODbL-1.0',
        attribution: '© OpenStreetMap contributors',
        style: { path: 'style/base.json', sha256: await sha256(utf8('{}')) },
        glyphs: { path: 'glyphs/', license: 'OFL-1.1' },
      },
    },
    routing: {
      walk_graph: 'routing/walk_graph.geojson',
      walk_graph_sha256: await hash('routing/walk_graph.geojson'),
      legs: 'routing/legs.json',
      legs_sha256: await hash('routing/legs.json'),
    },
    content: {
      sites: 'content/sites.json',
      sites_sha256: await hash('content/sites.json'),
      narrations: 'content/narrations.json',
      narrations_sha256: await hash('content/narrations.json'),
      itinerary: 'content/itinerary.json',
      itinerary_sha256: await hash('content/itinerary.json'),
    },
    attribution: { path: 'ATTRIBUTION.md', sha256: await hash('ATTRIBUTION.md') },
    textLicense: 'CC-BY-SA-4.0',
    withheld: [
      { site_id: SITE_A, field: 'reviews', reason: 'license_forbids_redistribution' },
      // Pre-removal index — the case the travel UI must not use for positioning.
      { site_id: SITE_A, field: 'notes[2]', reason: 'source_unavailable' },
    ],
  }
  mutate?.(raw)

  const canonical = canonicalize(raw)
  if (canonical === undefined) throw new Error('fixture manifest is not canonicalizable')
  raw.integrity = { manifest_sha256: await sha256(utf8(canonical)) }

  await storage.writeAt('manifest.json', 0, jsonBytes(raw))

  // Round-trip through the runtime's own parser so a fixture that the runtime would
  // reject fails here, loudly, rather than as a confusing assertion later.
  const { parseManifest } = await import('../../src/bundle/manifest')
  const manifest = parseManifest(raw)
  if (!manifest) throw new Error('fixture manifest does not parse as BundleManifestV1')

  return { storage, manifest, raw, tile: archive.tile }
}
