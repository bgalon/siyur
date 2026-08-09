/// <reference lib="webworker" />

/**
 * T049 — the OPFS tile reader. A **module worker** (`worker.format: 'es'`, pinned in
 * `vite.config.ts` since DU-00 precisely so this file needs no config change; ADR-0003
 * footgun 2 is that Vite's historical `iife` worker default cannot host ESM `import`).
 *
 * ## Why a worker at all
 *
 * `FileSystemSyncAccessHandle` is **worker-only** — the API does not exist on the main
 * thread, by design, because its reads are synchronous. That is also why it is the
 * right tool here: MapLibre asks for a few KB per tile and does it constantly, and a
 * synchronous read off an already-open handle has none of the per-call promise and
 * `File` re-materialisation overhead of `getFile().slice().arrayBuffer()`.
 *
 * ## The handle is opened once and held
 *
 * `createSyncAccessHandle()` takes an **exclusive lock** on the file: a second handle
 * on the same file — from this worker or another tab — throws `NoModificationAllowedError`.
 * So one handle is opened per archive and reused for every read, and `close` is a real
 * part of the protocol rather than politeness. This is also why the download manager
 * writes through `createWritable` on the main thread instead of through here: a held
 * read lock and a writer cannot coexist, and the archive is written once and read
 * forever.
 *
 * ## Short reads are normal, not an error
 *
 * The `pmtiles` reader opens by asking for **16384 bytes** whatever the archive's real
 * size — it wants the header plus, hopefully, the root directory in one round trip. A
 * small archive has fewer bytes than that. `read()` returns how many bytes it actually
 * filled, and this worker returns exactly that slice; returning the whole zero-padded
 * buffer instead would hand the parser trailing nulls it would read as directory
 * entries. (`FetchSource` has the same shape of problem and handles it with a `416`.)
 */

import { clampRange, type OpfsWorkerRequest, type OpfsWorkerResponse } from './opfs-protocol'

declare const self: DedicatedWorkerGlobalScope

/** The one handle, held open for the worker's lifetime. */
let handle: FileSystemSyncAccessHandle | null = null

async function open(dirs: readonly string[], name: string): Promise<number> {
  handle?.close()
  handle = null
  let directory = await navigator.storage.getDirectory()
  for (const segment of dirs) {
    directory = await directory.getDirectoryHandle(segment, { create: false })
  }
  const file = await directory.getFileHandle(name, { create: false })
  handle = await file.createSyncAccessHandle()
  return handle.getSize()
}

function read(offset: number, length: number): ArrayBuffer {
  if (!handle) throw new Error('OPFS archive not opened')
  // Clamp before allocating: a request past EOF must yield the available bytes (or
  // none), never a buffer of zeros that reads as valid-but-empty PMTiles structure.
  // The rule itself lives in `clampRange` so it is unit-testable without a worker.
  const { start, length: wanted } = clampRange(handle.getSize(), offset, length)
  const buffer = new ArrayBuffer(wanted)
  if (wanted === 0) return buffer
  const got = handle.read(new Uint8Array(buffer), { at: start })
  // `got < wanted` should not happen for a local file, but truncating to what was
  // actually read keeps a partially-evicted archive from presenting padding as data.
  return got === wanted ? buffer : buffer.slice(0, got)
}

self.onmessage = (event: MessageEvent<OpfsWorkerRequest>): void => {
  const request = event.data
  const fail = (error: unknown): void => {
    const response: OpfsWorkerResponse = {
      id: request.id,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    }
    self.postMessage(response)
  }

  try {
    switch (request.type) {
      case 'open':
        void open(request.dirs, request.name).then(
          (size) => self.postMessage({ id: request.id, ok: true, size } as OpfsWorkerResponse),
          fail,
        )
        return
      case 'read': {
        const data = read(request.offset, request.length)
        // Transferred, not copied — a tile read that copies is a tile read that
        // allocates twice per frame.
        self.postMessage({ id: request.id, ok: true, data } as OpfsWorkerResponse, [data])
        return
      }
      case 'close':
        handle?.close()
        handle = null
        self.postMessage({ id: request.id, ok: true } as OpfsWorkerResponse)
        return
    }
  } catch (error) {
    fail(error)
  }
}
