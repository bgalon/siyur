/**
 * The message contract between {@link ./opfs-source} (main thread) and
 * {@link ./opfs-worker} (module worker).
 *
 * It lives in its own module — imported by both sides — so the two cannot drift. A
 * worker boundary is a place where a renamed field fails at *runtime*, in a context
 * with no stack trace worth reading; a shared type turns that into a compile error.
 */

/** Open the archive and report its size. Sent once, before any read. */
export interface OpenRequest {
  readonly id: number
  readonly type: 'open'
  /** OPFS directory segments from the origin root down to the file's parent. */
  readonly dirs: readonly string[]
  readonly name: string
}

/** A byte range of the archive — one PMTiles directory or tile lookup. */
export interface ReadRequest {
  readonly id: number
  readonly type: 'read'
  readonly offset: number
  readonly length: number
}

/** Release the sync access handle. An open handle is an exclusive lock on the file. */
export interface CloseRequest {
  readonly id: number
  readonly type: 'close'
}

export type OpfsWorkerRequest = OpenRequest | ReadRequest | CloseRequest

export interface OpfsWorkerOk {
  readonly id: number
  readonly ok: true
  /** File size, on `open`. */
  readonly size?: number
  /** The bytes read, on `read` — **transferred**, never copied. */
  readonly data?: ArrayBuffer
}

export interface OpfsWorkerError {
  readonly id: number
  readonly ok: false
  readonly error: string
}

export type OpfsWorkerResponse = OpfsWorkerOk | OpfsWorkerError

/**
 * Clamp a requested range to what the file actually holds.
 *
 * **This is the whole of the short-read rule, and it lives here so it can be tested.**
 * The worker itself cannot be unit-tested — jsdom has neither `Worker` nor OPFS — so a
 * clamp written inline there would be exercised only by the e2e. `pmtiles` opens every
 * archive by asking for **16384 bytes** whatever its real size, wanting the 127-byte
 * header and, hopefully, the root directory in one round trip. A reader that answers
 * with a zero-padded 16 KB buffer hands the directory parser trailing nulls, which it
 * reads as varint entries: a plausible, wrong directory rather than a clean failure.
 *
 * Returns the byte offset to read from and how many bytes are actually available.
 */
export function clampRange(
  size: number,
  offset: number,
  length: number,
): { start: number; length: number } {
  const start = Math.max(0, Math.min(offset, size))
  return { start, length: Math.max(0, Math.min(length, size - start)) }
}
