/**
 * T048 — OPFS storage for a downloaded bundle, behind one narrow interface.
 *
 * {@link BundleStorage} is the ADR-0002 transport seam made concrete: the *read model*
 * does not change when the bundle moves from HTTP to OPFS, only what sits behind these
 * five methods. It is deliberately tiny — offset-addressed writes, whole-file reads,
 * a size probe and a delete — because everything above it (the download manager, the
 * integrity check, the travel surfaces) is then testable against an in-memory
 * implementation with no OPFS at all. jsdom has no OPFS, so without this seam the
 * whole slice would be e2e-only.
 *
 * **Tile reads do not come through here.** `getBytes(offset, length)` on a multi-MiB
 * archive is the hot path, and it belongs on `FileSystemSyncAccessHandle` inside a
 * worker (ADR-0003) — see {@link ./opfs-worker} and {@link ./opfs-source}. This
 * interface is for the small JSON artifacts and for writing the archive down.
 */

/** A per-bundle key-value store over paths relative to the bundle root. */
export interface BundleStorage {
  /** Stable identifier for this bundle's storage root — the `opfs://…` protocol key. */
  readonly key: string
  /** Bytes already stored at `path`; `0` when the file does not exist. */
  size(path: string): Promise<number>
  /** Write `bytes` at `offset`, creating intermediate directories and the file. */
  writeAt(path: string, offset: number, bytes: Uint8Array): Promise<void>
  /** Whole file, or `null` when absent. */
  read(path: string): Promise<Uint8Array | null>
  /**
   * The first `length` bytes at `offset`, or `null` when absent.
   *
   * Exists so the launch check can inspect a 200 MB archive's 127-byte header without
   * materialising the archive — `read()` on the PMTiles file would allocate the whole
   * budget at startup, on the device least able to afford it.
   */
  readRange(path: string, offset: number, length: number): Promise<Uint8Array | null>
  /** Remove `path` if present. Never throws for a missing file. */
  remove(path: string): Promise<void>
}

/**
 * Ask the browser to make this origin's storage persistent.
 *
 * **This is a request, not a guarantee, and the caller must not treat it as one.**
 * Chromium grants it on heuristics; WebKit evicts unpersisted origin storage after
 * ~7 days regardless (the risk ADR-0002 defers by going Chromium-first). A refused
 * request is therefore *not* an error — it means the launch-time integrity check
 * (T053) is now the only thing standing between an evicted bundle and a partially
 * rendered day, which is exactly why that check exists and is not optional.
 *
 * Returns `false` on any browser without the API rather than throwing, so the download
 * path stays usable in a test environment and in a private-mode context.
 */
export async function requestPersistentStorage(): Promise<boolean> {
  const storage = globalThis.navigator?.storage
  if (!storage?.persist) return false
  try {
    // `persisted()` first: re-requesting an already-granted permission is harmless but
    // can re-prompt on some engines, and this runs on every launch.
    if (storage.persisted && (await storage.persisted())) return true
    return await storage.persist()
  } catch {
    return false
  }
}

/** Split `a/b/c.json` into `['a','b']` + `'c.json'`, rejecting traversal. */
function splitPath(path: string): { dirs: string[]; name: string } {
  const parts = path.split('/').filter((p) => p !== '' && p !== '.')
  if (parts.length === 0 || parts.includes('..')) {
    // A manifest is a downloaded artifact: a `..` in a manifest path is an attempt to
    // write outside the bundle root, so it is refused rather than normalised away.
    throw new Error(`unsafe bundle path: ${path}`)
  }
  const name = parts.pop() as string
  return { dirs: parts, name }
}

/** OPFS-backed {@link BundleStorage}, rooted at `bundles/<bundleId>/`. */
export class OpfsBundleStorage implements BundleStorage {
  readonly key: string
  /**
   * The OPFS directory segments from the **origin root** down to this bundle.
   *
   * Public because the tile worker resolves handles from `navigator.storage.getDirectory()`
   * and therefore needs the absolute path, not a bundle-relative one. Exposing it here —
   * rather than letting the caller retype `['bundles', bundleId]` — is what keeps the
   * worker reading the same directory this class wrote into. A mismatch would surface as
   * a `NotFoundError` inside a worker, which is about the least legible place for it.
   */
  readonly rootDirs: readonly string[]

  constructor(bundleId: string, rootName = 'bundles') {
    // The key doubles as the `pmtiles://` protocol target (see `./protocol`), so it
    // must be a stable, unique string — MapLibre matches it verbatim. Both are derived
    // from `rootDirs` so the two can never describe different directories.
    this.rootDirs = [rootName, bundleId]
    this.key = `opfs://${this.rootDirs.join('/')}`
  }

  private async dir(dirs: string[], create: boolean): Promise<FileSystemDirectoryHandle> {
    let handle = await navigator.storage.getDirectory()
    for (const segment of [...this.rootDirs, ...dirs]) {
      handle = await handle.getDirectoryHandle(segment, { create })
    }
    return handle
  }

  private async file(path: string, create: boolean): Promise<FileSystemFileHandle | null> {
    const { dirs, name } = splitPath(path)
    try {
      const parent = await this.dir(dirs, create)
      return await parent.getFileHandle(name, { create })
    } catch (error) {
      // `NotFoundError` is the expected answer for "not downloaded yet" and must not
      // propagate — resumability is built on asking this question about absent files.
      if (!create && (error as DOMException)?.name === 'NotFoundError') return null
      throw error
    }
  }

  async size(path: string): Promise<number> {
    const handle = await this.file(path, false)
    if (!handle) return 0
    return (await handle.getFile()).size
  }

  async writeAt(path: string, offset: number, bytes: Uint8Array): Promise<void> {
    const handle = await this.file(path, true)
    if (!handle) throw new Error(`could not open ${path} for writing`)
    // `keepExistingData: true` is what makes a resumed download resume rather than
    // restart: without it `createWritable` truncates, and every interruption would
    // silently throw away everything already fetched.
    const writable = await handle.createWritable({ keepExistingData: true })
    try {
      await writable.write({ type: 'write', position: offset, data: bytes })
    } finally {
      await writable.close()
    }
  }

  async read(path: string): Promise<Uint8Array | null> {
    const handle = await this.file(path, false)
    if (!handle) return null
    return new Uint8Array(await (await handle.getFile()).arrayBuffer())
  }

  async readRange(path: string, offset: number, length: number): Promise<Uint8Array | null> {
    const handle = await this.file(path, false)
    if (!handle) return null
    const file = await handle.getFile()
    return new Uint8Array(await file.slice(offset, offset + length).arrayBuffer())
  }

  async remove(path: string): Promise<void> {
    const { dirs, name } = splitPath(path)
    try {
      const parent = await this.dir(dirs, false)
      await parent.removeEntry(name)
    } catch (error) {
      if ((error as DOMException)?.name !== 'NotFoundError') throw error
    }
  }
}

/** Decode a stored artifact as UTF-8 text, or `null` when it is absent. */
export async function readText(storage: BundleStorage, path: string): Promise<string | null> {
  const bytes = await storage.read(path)
  return bytes === null ? null : new TextDecoder().decode(bytes)
}

/** Decode a stored artifact as JSON, or `null` when absent or unparseable. */
export async function readJson(storage: BundleStorage, path: string): Promise<unknown> {
  const text = await readText(storage, path)
  if (text === null) return null
  try {
    return JSON.parse(text)
  } catch {
    // A truncated artifact parses as a syntax error, which is a corruption signal, not
    // a programming error. The caller (T053) turns it into "unusable bundle".
    return null
  }
}
