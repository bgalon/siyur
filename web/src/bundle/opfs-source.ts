/**
 * T049/T050 — a `pmtiles` v4 `Source` whose bytes come out of OPFS.
 *
 * `pmtiles` ships `FetchSource` (HTTP range requests) and `FileSource` (a picked
 * `File`). Neither fits: the archive is in OPFS and must be read with
 * `FileSystemSyncAccessHandle`, which only exists inside a worker. `Source` is a
 * two-method interface (`getKey`, `getBytes`) and implementing it is the whole of the
 * ADR-0002 transport swap — `PMTiles`, `Protocol`, the cache and the style above it do
 * not change, and neither does the read model.
 *
 * **The byte transport is injected**, not hardcoded to `Worker`. jsdom has no workers
 * and no OPFS, so a source bound to `new Worker(...)` would be untestable outside a
 * browser and this slice would have no unit tests at all — only the e2e that already
 * exists. {@link RangeReader} is the seam; {@link WorkerRangeReader} is the real
 * implementation, and a test supplies an in-memory one over the same archive bytes.
 */

import type { Source } from 'pmtiles'

import type { OpfsWorkerRequest, OpfsWorkerResponse } from './opfs-protocol'

/** Whatever can hand back a byte range of the archive. */
export interface RangeReader {
  /** Open the archive; resolves to its size in bytes. */
  open(): Promise<number>
  getBytes(offset: number, length: number): Promise<ArrayBuffer>
  close(): Promise<void>
}

/** A {@link RangeReader} backed by the {@link ./opfs-worker} module worker. */
export class WorkerRangeReader implements RangeReader {
  private nextId = 1
  private readonly pending = new Map<
    number,
    { resolve: (value: OpfsWorkerOkPayload) => void; reject: (reason: Error) => void }
  >()

  constructor(
    private readonly worker: Worker,
    private readonly dirs: readonly string[],
    private readonly name: string,
  ) {
    this.worker.onmessage = (event: MessageEvent<OpfsWorkerResponse>): void => {
      const message = event.data
      const entry = this.pending.get(message.id)
      if (!entry) return
      this.pending.delete(message.id)
      if (message.ok) entry.resolve({ size: message.size, data: message.data })
      else entry.reject(new Error(message.error))
    }
    // A worker that dies takes every in-flight read with it. Without this the tile
    // requests simply never settle and the map stops painting with no error at all —
    // the offline failure mode that is hardest to tell apart from "no tiles here".
    this.worker.onerror = (event: ErrorEvent): void => {
      const error = new Error(event.message || 'OPFS worker failed')
      for (const [, entry] of this.pending) entry.reject(error)
      this.pending.clear()
    }
  }

  private send(request: OpfsWorkerRequest): Promise<OpfsWorkerOkPayload> {
    return new Promise((resolve, reject) => {
      this.pending.set(request.id, { resolve, reject })
      this.worker.postMessage(request)
    })
  }

  async open(): Promise<number> {
    const { size } = await this.send({
      id: this.nextId++,
      type: 'open',
      dirs: this.dirs,
      name: this.name,
    })
    return size ?? 0
  }

  async getBytes(offset: number, length: number): Promise<ArrayBuffer> {
    const { data } = await this.send({ id: this.nextId++, type: 'read', offset, length })
    return data ?? new ArrayBuffer(0)
  }

  async close(): Promise<void> {
    await this.send({ id: this.nextId++, type: 'close' })
    this.worker.terminate()
  }
}

interface OpfsWorkerOkPayload {
  readonly size?: number
  readonly data?: ArrayBuffer
}

/**
 * The `pmtiles` `Source` over OPFS.
 *
 * `getKey()` is **not decorative**: `Protocol.add()` indexes instances by this string,
 * and `Protocol.tile` parses `pmtiles://<key>/z/x/y` and looks `<key>` up. On a miss it
 * silently constructs a `new PMTiles(<key>)` — which is a **`FetchSource`, i.e. a
 * network request**. So a key that does not match the style URL byte-for-byte does not
 * fail loudly; it quietly reintroduces the network on the one surface whose entire
 * promise is that there isn't one. {@link ./protocol!bundleTileUrl} derives both ends
 * from this value so they cannot disagree.
 */
export class OpfsTileSource implements Source {
  private opened: Promise<number> | null = null

  constructor(
    private readonly reader: RangeReader,
    private readonly key: string,
  ) {}

  getKey(): string {
    return this.key
  }

  async getBytes(offset: number, length: number): Promise<{ data: ArrayBuffer }> {
    // Open lazily and exactly once. `pmtiles` fires several reads concurrently for the
    // first tile; opening per-call would race for the exclusive sync-access lock and
    // lose with `NoModificationAllowedError`.
    this.opened ??= this.reader.open()
    await this.opened
    return { data: await this.reader.getBytes(offset, length) }
  }

  async close(): Promise<void> {
    if (this.opened) await this.reader.close()
    this.opened = null
  }
}

/**
 * The part of a bundle store the tile worker needs: where it is, and what to call it.
 *
 * Taking the *storage object* rather than two loose strings is deliberate —
 * `OpfsBundleStorage` satisfies this structurally, so the worker cannot end up reading a
 * different directory from the one the download manager wrote into.
 */
export interface OpfsBundleLocation {
  readonly key: string
  readonly rootDirs: readonly string[]
}

/**
 * Build the worker-backed source for a bundle's archive at manifest-relative `path`.
 *
 * `new URL('./opfs-worker.ts', import.meta.url)` with `{ type: 'module' }` is the form
 * Vite statically analyses to emit the worker chunk; a string path or a computed URL is
 * not analysable and produces a worker that 404s only in the built app. (Verified in
 * `dist/`: the emitted call carries `{type:"module"}` — ADR-0003 footgun 2.)
 *
 * The worker resolves directories from `navigator.storage.getDirectory()` and has no
 * notion of a bundle, so the store's root segments are prepended to the manifest path.
 */
export function createOpfsTileSource(
  location: OpfsBundleLocation,
  path: string,
): OpfsTileSource {
  const segments = path.split('/').filter((s) => s !== '')
  const name = segments.pop() ?? path
  const worker = new Worker(new URL('./opfs-worker.ts', import.meta.url), { type: 'module' })
  return new OpfsTileSource(
    new WorkerRangeReader(worker, [...location.rootDirs, ...segments], name),
    `${location.key}/${path}`,
  )
}
