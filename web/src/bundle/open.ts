/**
 * T053 — the launch-time integrity check.
 *
 * **Fail closed, never toward "looks fine."** A bundle that is corrupted, truncated or
 * only partly downloaded reports as **unusable** (FR-020). It never renders the part of
 * the day that happened to survive: a traveller with no connectivity cannot tell a
 * three-stop day from a five-stop day whose last two stops were silently dropped, and
 * the failure mode of guessing wrong is being in the wrong place with no way to check.
 *
 * ## What is checked, and what each check is actually guarding
 *
 * 1. **The manifest parses and its own digest matches** (RFC 8785 — see `./manifest`).
 *    This is the iOS storage-eviction guard the card names: OPFS can be evicted or
 *    partially reclaimed between sessions, and a manifest that no longer hashes to its
 *    own `integrity.manifest_sha256` is the first observable symptom.
 * 2. **Every path the manifest names resolves to a non-empty file.** A missing artifact
 *    is the *normal* shape of eviction and of an interrupted download, and it is the
 *    one failure the digest check cannot see — the manifest is intact and describes a
 *    bundle that is not there.
 * 3. **Every small artifact hashes to what the manifest claims.** One hash per
 *    artifact, so a failure names the file (the card's rule, and its reason).
 *
 * ## Why the PMTiles archive is not fully hashed at launch by default
 *
 * The archive dominates the ≤200 MB budget. Hashing it on every launch would cost
 * seconds of startup on the one device class that has the least to spare, to re-check
 * bytes that were already verified against the same hash **at download** (see
 * `./download`). Instead the launch check proves the archive is present, non-empty and
 * structurally a PMTiles v3 file — its 7-byte magic and spec version, which is what a
 * truncated or zero-filled file fails. `deep: true` hashes it too, and the corrupted-
 * bundle e2e (T057) is free to use it.
 *
 * This is a deliberate, named trade — not an oversight. If a partially corrupted
 * *interior* of an archive ever shows up in practice, the honest fix is to make `deep`
 * the default and pay the cost, not to add a cheaper approximation.
 */

import { parseManifest, sha256Hex, verifyManifestDigest } from './manifest'
import { readJson, type BundleStorage } from './storage'
import { manifestArtifacts, type BundleManifestV1 } from './types'

/** Where the manifest lives inside a bundle root. */
export const MANIFEST_PATH = 'manifest.json'

/** Why a bundle is unusable. Closed set — the UI maps each to a distinct message. */
export type BundleFailure =
  | 'manifest_missing'
  | 'manifest_unreadable'
  | 'manifest_digest_mismatch'
  | 'artifact_missing'
  | 'artifact_corrupt'

export type OpenBundleResult =
  | { readonly ok: true; readonly manifest: BundleManifestV1 }
  | { readonly ok: false; readonly failure: BundleFailure; readonly detail: string }

export interface OpenBundleOptions {
  readonly storage: BundleStorage
  /** Also hash the full PMTiles archive. Off by default — see the module note. */
  readonly deep?: boolean
}

/** PMTiles v3 magic: the ASCII bytes `PMTiles` followed by a spec-version byte. */
const PMTILES_MAGIC = [0x50, 0x4d, 0x54, 0x69, 0x6c, 0x65, 0x73]

function looksLikePmtiles(bytes: Uint8Array): boolean {
  if (bytes.byteLength < 127) return false
  if (!PMTILES_MAGIC.every((b, i) => bytes[i] === b)) return false
  const specVersion = bytes[7]
  return specVersion !== undefined && specVersion > 0 && specVersion <= 3
}

/**
 * Open a downloaded bundle, or say why it cannot be used.
 *
 * Returns a result rather than throwing: "this bundle is unusable" is an expected
 * state with a specific screen behind it, not an exception.
 */
export async function openBundle(options: OpenBundleOptions): Promise<OpenBundleResult> {
  const { storage, deep = false } = options

  const raw = await readJson(storage, MANIFEST_PATH)
  if (raw === null) {
    // `readJson` returns null both for "absent" and for "not JSON". Distinguish them,
    // because "never downloaded" and "downloaded and corrupted" are different screens.
    const present = (await storage.size(MANIFEST_PATH)) > 0
    return present
      ? { ok: false, failure: 'manifest_unreadable', detail: MANIFEST_PATH }
      : { ok: false, failure: 'manifest_missing', detail: MANIFEST_PATH }
  }

  // Digest FIRST, over the raw parsed value — before narrowing drops anything this
  // client does not model (an M2 `schematic`, say), which would change the bytes.
  if (!(await verifyManifestDigest(raw))) {
    return { ok: false, failure: 'manifest_digest_mismatch', detail: MANIFEST_PATH }
  }

  const manifest = parseManifest(raw)
  if (!manifest) return { ok: false, failure: 'manifest_unreadable', detail: MANIFEST_PATH }

  const archivePath = manifest.tiles.pmtiles.path
  for (const artifact of manifestArtifacts(manifest)) {
    // Presence first, and by size — this is the check that catches eviction and an
    // interrupted download, and it costs one stat rather than one whole file.
    if ((await storage.size(artifact.path)) === 0) {
      return { ok: false, failure: 'artifact_missing', detail: artifact.path }
    }
    if (artifact.path === archivePath && !deep) {
      const head = await storage.readRange(artifact.path, 0, 127)
      if (!head || !looksLikePmtiles(head)) {
        return { ok: false, failure: 'artifact_corrupt', detail: artifact.path }
      }
      continue
    }
    const bytes = await storage.read(artifact.path)
    if (!bytes || (await sha256Hex(bytes)) !== artifact.sha256) {
      return { ok: false, failure: 'artifact_corrupt', detail: artifact.path }
    }
  }

  return { ok: true, manifest }
}
