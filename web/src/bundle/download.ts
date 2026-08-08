/**
 * T048 — the download manager: fetch the whole archive into OPFS, resumably.
 *
 * The bundle is up to 200 MB over whatever connection the traveller had before they
 * left (`docs/data/bundle-manifest.md` § `size_bytes`), so an interrupted transfer is
 * the normal case, not the exceptional one. Resume is by HTTP **range**: what is
 * already in OPFS is the offset to ask for next, so state lives in the filesystem and
 * nothing has to survive a page reload.
 *
 * **Three server behaviours, all of which happen.** `206` is the resume we asked for.
 * `200` means the server *ignored* `Range` and is sending the whole object from byte
 * zero — appending that to a partial file yields a plausible, wrong, hash-failing
 * artifact, so the partial is discarded first. `416` means the offset is at or past
 * the end, i.e. the file is already whole. Treating any of the three as another is how
 * a corrupt bundle gets written and only discovered at launch.
 *
 * **Every artifact is verified against its own hash the moment it lands**, and a
 * mismatch *deletes* the file. The card's rule is one hash per artifact precisely so a
 * failure names the file that broke; keeping the bad bytes would make the next resume
 * append onto garbage forever.
 */

import { sha256Hex } from './manifest'
import type { BundleStorage } from './storage'
import { manifestArtifacts, type BundleManifestV1 } from './types'

/** Progress for one artifact. `total` is `null` until the server reveals a length. */
export interface DownloadProgress {
  readonly path: string
  readonly received: number
  readonly total: number | null
}

export interface DownloadOptions {
  /** Where the bundle's files live, e.g. `https://…/bundles/bnd_01J…/`. */
  readonly baseUrl: string
  readonly storage: BundleStorage
  /** Injectable for tests; defaults to the global `fetch`. */
  readonly fetchImpl?: typeof fetch
  readonly signal?: AbortSignal
  readonly onProgress?: (progress: DownloadProgress) => void
}

/** Thrown when an artifact's bytes do not match the hash the manifest claims. */
export class ArtifactIntegrityError extends Error {
  constructor(
    readonly path: string,
    readonly expected: string,
    readonly actual: string,
  ) {
    super(`${path}: expected sha256 ${expected}, got ${actual}`)
    this.name = 'ArtifactIntegrityError'
  }
}

function join(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`
}

/**
 * Fetch one artifact into `storage`, resuming from whatever is already there.
 *
 * Returns the number of bytes newly transferred — `0` means the file was already
 * complete, which is what makes calling this on every launch cheap.
 */
export async function downloadArtifact(
  path: string,
  options: DownloadOptions,
): Promise<number> {
  const { baseUrl, storage, signal, onProgress } = options
  const doFetch = options.fetchImpl ?? globalThis.fetch
  let offset = await storage.size(path)

  const response = await doFetch(join(baseUrl, path), {
    signal: signal ?? null,
    headers: offset > 0 ? { Range: `bytes=${offset}-` } : {},
  })

  // 416 Range Not Satisfiable with a non-zero offset means we asked past the end:
  // the file is already whole. Any other 4xx/5xx is a real failure.
  if (response.status === 416 && offset > 0) return 0
  if (!response.ok && response.status !== 206) {
    throw new Error(`${path}: HTTP ${response.status}`)
  }
  if (offset > 0 && response.status !== 206) {
    // The server ignored `Range` and restarted the object. Throw the partial away —
    // appending a from-zero body onto it produces a longer file that hashes wrong.
    await storage.remove(path)
    offset = 0
  }

  const declared = Number(response.headers.get('Content-Length'))
  const total = Number.isFinite(declared) && declared > 0 ? offset + declared : null
  let received = offset

  const write = async (chunk: Uint8Array): Promise<void> => {
    if (chunk.byteLength === 0) return
    await storage.writeAt(path, received, chunk)
    received += chunk.byteLength
    onProgress?.({ path, received, total })
  }

  // Stream when the environment gives us a body reader — a 200 MB archive must not be
  // buffered whole in memory. `body` is absent in some test doubles and older engines,
  // where a single `arrayBuffer()` is correct and the size is small by construction.
  const body = response.body
  if (body) {
    const reader = body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      if (value) await write(value)
    }
  } else {
    await write(new Uint8Array(await response.arrayBuffer()))
  }

  return received - offset
}

/**
 * Verify one stored artifact against the manifest's hash for it.
 *
 * On mismatch the file is removed and {@link ArtifactIntegrityError} is thrown — fail
 * closed, and leave nothing behind that a resume would build on.
 */
export async function verifyArtifact(
  storage: BundleStorage,
  path: string,
  expected: string,
): Promise<void> {
  const bytes = await storage.read(path)
  if (bytes === null) throw new ArtifactIntegrityError(path, expected, 'missing')
  const actual = await sha256Hex(bytes)
  if (actual !== expected.toLowerCase()) {
    await storage.remove(path)
    throw new ArtifactIntegrityError(path, expected.toLowerCase(), actual)
  }
}

/**
 * Download every artifact the manifest names, verifying each as it lands.
 *
 * Sequential on purpose: the artifacts share one storage root and one connection, and
 * a traveller on a hotel wifi gets a more useful progress signal from one file
 * finishing than from seven files each 14% done.
 */
export async function downloadBundle(
  manifest: BundleManifestV1,
  options: DownloadOptions,
): Promise<void> {
  for (const artifact of manifestArtifacts(manifest)) {
    await downloadArtifact(artifact.path, options)
    await verifyArtifact(options.storage, artifact.path, artifact.sha256)
  }
}
