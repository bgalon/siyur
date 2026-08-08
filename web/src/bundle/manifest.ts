/**
 * T053 — the launch-time integrity check, and the canonicalization it rests on.
 *
 * ## The digest rule, restated because getting it slightly wrong is undiagnosable
 *
 * `integrity.manifest_sha256` is the SHA-256 of the manifest serialized per
 * **RFC 8785 (JSON Canonicalization Scheme)** with the **`integrity` key removed**
 * before serialization — *removed*, not `null`, not `""`. Lowercase hex.
 *
 * The writer is Python (`rfc8785`) and this verifier is TypeScript (`canonicalize`,
 * Apache-2.0, pinned per ADR-0007). They must agree byte-for-byte, and by default
 * they do **not** — `docs/data/bundle-manifest.md` and ADR-0025 A5 record the two
 * divergences that a hand-rolled `JSON.stringify` with sorted keys walks straight into:
 *
 *   - **escaping**, which fires on *every* manifest: `json.dumps` defaults to
 *     `ensure_ascii=True` and escapes the `©` that every ODbL attribution carries;
 *   - **number formatting**, which fires *intermittently*: Python renders `28.0`
 *     where JavaScript renders `28`, but only when a bbox bound lands on a whole
 *     degree — so it breaks for some areas and not others, which reads like
 *     corruption rather than like a serialization bug.
 *
 * A mismatch surfaces **offline, on the traveller's device, with no way to diagnose
 * it**. So: no hand-rolling. RFC 8785 or nothing.
 *
 * ## Why the digest is computed over the RAW parsed JSON, never over our own type
 *
 * {@link parseManifest} narrows an unknown blob to {@link BundleManifestV1} and drops
 * what it does not model. Hashing *that* would be a bug with a long fuse: an M2 bundle
 * carrying `schematic` (a field this client has no reason to model yet) would fail its
 * own integrity check on an M1 client, and the failure would look like corruption.
 * {@link manifestDigest} therefore takes the **raw** parsed value. The narrowed type is
 * for rendering; the raw value is for hashing. They are not interchangeable.
 */

import canonicalize from 'canonicalize'

import {
  WITHHELD_REASONS,
  type BundleManifestV1,
  type HashedArtifact,
  type TileSourceV1,
  type WithheldEntry,
  type WithheldReason,
} from './types'

/** Lowercase hex SHA-256 of raw bytes — the card's hash format for every artifact. */
export async function sha256Hex(bytes: BufferSource): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * The exact bytes the manifest digest is taken over: RFC 8785 canonical JSON of the
 * manifest **with `integrity` removed** (a hash cannot cover itself).
 *
 * Exported so a test can assert the *rule* — "the `integrity` key does not appear" —
 * rather than a literal string this implementation happens to emit.
 */
export function canonicalManifestBytes(raw: unknown): Uint8Array {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new TypeError('manifest is not a JSON object')
  }
  // Shallow copy + `delete`: the key must be ABSENT, not nulled or emptied. A
  // `{...raw, integrity: undefined}` spread would also work in JS but survives as an
  // own key under some serializers — `delete` is unambiguous.
  const withoutIntegrity: Record<string, unknown> = { ...(raw as Record<string, unknown>) }
  delete withoutIntegrity.integrity

  const canonical = canonicalize(withoutIntegrity)
  if (canonical === undefined) throw new TypeError('manifest is not canonicalizable JSON')
  return new TextEncoder().encode(canonical)
}

/** SHA-256 of {@link canonicalManifestBytes} — what `integrity.manifest_sha256` holds. */
export async function manifestDigest(raw: unknown): Promise<string> {
  return sha256Hex(canonicalManifestBytes(raw))
}

/**
 * Does the manifest's own `integrity.manifest_sha256` match what the bytes say?
 *
 * Case-insensitive on the *claimed* side only: the card fixes the digest as lowercase
 * hex, and {@link sha256Hex} emits lowercase, so a manifest that shouts its hash is
 * accepted rather than reported as corrupt. Nothing else about the value is repaired.
 */
export async function verifyManifestDigest(raw: unknown): Promise<boolean> {
  const claimed = (raw as { integrity?: { manifest_sha256?: unknown } } | null)?.integrity
    ?.manifest_sha256
  if (typeof claimed !== 'string' || claimed.trim() === '') return false
  return claimed.trim().toLowerCase() === (await manifestDigest(raw))
}

/* ------------------------------------------------------- narrowing ---------- */

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() !== '' ? v : null
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function hashedArtifact(v: unknown, pathKey = 'path', hashKey = 'sha256'): HashedArtifact | null {
  if (!isRecord(v)) return null
  const path = str(v[pathKey])
  const sha256 = str(v[hashKey])
  return path && sha256 ? { path, sha256: sha256.toLowerCase() } : null
}

/**
 * EPSG:4326 `[west, south, east, north]` — the card's CRS discipline.
 *
 * Two things are checkable and both are refused rather than corrected: a bound outside
 * the coordinate ranges, and a mis-ordered extent (`west > east`, `south > north`).
 *
 * **What is deliberately NOT claimed: this does not detect a lon/lat swap.** For most
 * inhabited places both values fall inside ±90, so a swapped `[36.44, 28.0, …]` is
 * indistinguishable from a real bbox by inspection alone. The defence against a swap is
 * upstream — coordinates are computed in PostGIS/shapely and never emitted by a model
 * (AGENTS.md geo rules) — not here. Saying so keeps a future reader from trusting a
 * guard that does not exist.
 */
function bbox(v: unknown): readonly [number, number, number, number] | null {
  if (!Array.isArray(v) || v.length !== 4) return null
  const out = v.map(num)
  if (out.some((n) => n === null)) return null
  const [w, s, e, n] = out as [number, number, number, number]
  if (w < -180 || w > 180 || e < -180 || e > 180) return null
  if (s < -90 || s > 90 || n < -90 || n > 90) return null
  if (w > e || s > n) return null
  return [w, s, e, n]
}

function tileSource(v: unknown): TileSourceV1 | null {
  if (!isRecord(v)) return null
  const path = str(v.path)
  const sha256 = str(v.sha256)
  const box = bbox(v.bbox)
  const maxzoom = num(v.maxzoom)
  // The card's required subset. Anything short of it cannot render a basemap at all.
  if (!path || !sha256 || !box || maxzoom === null) return null
  return {
    path,
    sha256: sha256.toLowerCase(),
    bbox: box,
    maxzoom,
    ...(num(v.minzoom) !== null ? { minzoom: num(v.minzoom) as number } : {}),
    ...(str(v.format) ? { format: str(v.format) as string } : {}),
    ...(str(v.schema_ver) ? { schema_ver: str(v.schema_ver) as string } : {}),
    ...(str(v.build_source) ? { build_source: str(v.build_source) as string } : {}),
    ...(str(v.build_date) ? { build_date: str(v.build_date) as string } : {}),
    ...(str(v.tile_license) ? { tile_license: str(v.tile_license) as string } : {}),
    ...(str(v.attribution) ? { attribution: str(v.attribution) as string } : {}),
    ...(hashedArtifact(v.style) ? { style: hashedArtifact(v.style) as HashedArtifact } : {}),
    ...(isRecord(v.glyphs) && str(v.glyphs.path)
      ? {
          glyphs: {
            path: str(v.glyphs.path) as string,
            ...(str(v.glyphs.license) ? { license: str(v.glyphs.license) as string } : {}),
          },
        }
      : {}),
  }
}

function isWithheldReason(v: unknown): v is WithheldReason {
  return typeof v === 'string' && (WITHHELD_REASONS as readonly string[]).includes(v)
}

/**
 * Narrow the `withheld` list.
 *
 * An entry whose `reason` is not in the closed enum is **dropped, not coerced**. The
 * enum is closed precisely so `withheld` cannot become a free-text channel out of a
 * downloadable artifact (ADR-0025 A3); mapping an unknown reason onto a known one would
 * hand that channel back. A dropped entry costs one "needs connectivity" affordance,
 * which is the safe direction — the value is absent either way.
 */
function withheld(v: unknown): readonly WithheldEntry[] {
  if (!Array.isArray(v)) return []
  const out: WithheldEntry[] = []
  for (const entry of v) {
    if (!isRecord(entry)) continue
    const site_id = str(entry.site_id)
    const field = str(entry.field)
    if (site_id && field && isWithheldReason(entry.reason)) {
      out.push({ site_id, field, reason: entry.reason })
    }
  }
  return out
}

/**
 * Narrow an unknown blob to a `BundleManifestV1`, or `null` if it is not one.
 *
 * **Fail closed.** Every field the travel UI needs to resolve a path is required; a
 * manifest missing any of them yields `null`, and {@link ./index!openBundle} reports the
 * bundle unusable rather than rendering the part of the day that happened to survive
 * (FR-020). `textLicense` is the one field that is legitimately `null` — ADR-0024 makes
 * it `null` exactly when `content.narrations` is empty — so a *missing* `textLicense`
 * key and an explicit `null` both read as "no bundled narration text", and neither is a
 * reason to refuse the bundle.
 */
export function parseManifest(raw: unknown): BundleManifestV1 | null {
  if (!isRecord(raw)) return null
  if (raw.schema_ver !== 'BundleManifestV1') return null

  const bundle_id = str(raw.bundle_id)
  const itinerary_id = str(raw.itinerary_id)
  const created_at = str(raw.created_at)
  const size_bytes = num(raw.size_bytes)
  const pmtiles = isRecord(raw.tiles) ? tileSource(raw.tiles.pmtiles) : null
  const attribution = hashedArtifact(raw.attribution)
  const manifest_sha256 = isRecord(raw.integrity) ? str(raw.integrity.manifest_sha256) : null

  const r = isRecord(raw.routing) ? raw.routing : {}
  const walk_graph = str(r.walk_graph)
  const walk_graph_sha256 = str(r.walk_graph_sha256)
  const legs = str(r.legs)
  const legs_sha256 = str(r.legs_sha256)

  const c = isRecord(raw.content) ? raw.content : {}
  const sites = str(c.sites)
  const sites_sha256 = str(c.sites_sha256)
  const narrations = str(c.narrations)
  const narrations_sha256 = str(c.narrations_sha256)
  const itinerary = str(c.itinerary)
  const itinerary_sha256 = str(c.itinerary_sha256)

  if (
    !bundle_id ||
    !itinerary_id ||
    !created_at ||
    size_bytes === null ||
    !pmtiles ||
    !attribution ||
    !manifest_sha256 ||
    !walk_graph ||
    !walk_graph_sha256 ||
    !legs ||
    !legs_sha256 ||
    !sites ||
    !sites_sha256 ||
    !narrations ||
    !narrations_sha256 ||
    !itinerary ||
    !itinerary_sha256
  ) {
    return null
  }

  return {
    bundle_id,
    itinerary_id,
    created_at,
    size_bytes,
    schema_ver: 'BundleManifestV1',
    tiles: { pmtiles },
    routing: {
      walk_graph,
      walk_graph_sha256: walk_graph_sha256.toLowerCase(),
      legs,
      legs_sha256: legs_sha256.toLowerCase(),
    },
    content: {
      sites,
      sites_sha256: sites_sha256.toLowerCase(),
      narrations,
      narrations_sha256: narrations_sha256.toLowerCase(),
      itinerary,
      itinerary_sha256: itinerary_sha256.toLowerCase(),
    },
    attribution,
    textLicense: str(raw.textLicense),
    withheld: withheld(raw.withheld),
    integrity: { manifest_sha256: manifest_sha256.toLowerCase() },
  }
}
