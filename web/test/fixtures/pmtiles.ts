/**
 * A **real, minimal PMTiles v3 archive**, built in memory.
 *
 * Not a stub and not a recorded blob: the OPFS transport swap's whole risk is that
 * `pmtiles` reads bytes in a specific shape (a 127-byte header, a varint directory, a
 * 16 KB opening read that overruns a small file), and a fake source that returns
 * whatever it is asked for proves none of that. Building the archive here means
 * `PMTiles.getZxy` in the test is doing exactly what it does in the browser.
 *
 * Layout, per the PMTiles v3 spec:
 *
 *     [ header: 127 bytes ][ root directory ][ JSON metadata ][ tile data ]
 *
 * Compression is `None` (`1`) for both the internal structures and the tile so the test
 * asserts the transport, not `DecompressionStream`.
 */

/** Protobuf-style varint, the encoding a PMTiles directory uses throughout. */
function varint(value: number): number[] {
  const out: number[] = []
  let v = value
  while (v >= 0x80) {
    out.push((v & 0x7f) | 0x80)
    v = Math.floor(v / 128)
  }
  out.push(v)
  return out
}

/**
 * Serialize a one-entry root directory for tile id 0 (z=0/x=0/y=0).
 *
 * The five columns are written in the order the reader consumes them: count, tile-id
 * deltas, run lengths, byte lengths, then offsets — where an offset is stored as
 * `offset + 1`, with `0` meaning "contiguous with the previous entry".
 */
function rootDirectory(tileLength: number): Uint8Array {
  return new Uint8Array([
    ...varint(1), // one entry
    ...varint(0), // tileId delta from 0 ⇒ tile id 0 ⇒ z0/x0/y0
    ...varint(1), // runLength
    ...varint(tileLength),
    ...varint(1), // offset 0, stored as offset + 1
  ])
}

export interface SyntheticArchive {
  readonly bytes: Uint8Array
  /** The tile body stored at z0/x0/y0 — what `getZxy(0, 0, 0)` must hand back. */
  readonly tile: Uint8Array
}

/**
 * Build the archive. `bbox` is EPSG:4326 `[west, south, east, north]` and is written
 * into the header as the archive's own bounds, so a test can prove the reader saw the
 * real header rather than a default.
 */
export function buildPmtiles(
  tile: Uint8Array = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]),
  bbox: readonly [number, number, number, number] = [28.216, 36.44, 28.232, 36.451],
): SyntheticArchive {
  const metadata = new TextEncoder().encode(JSON.stringify({ name: 'synthetic' }))
  const directory = rootDirectory(tile.byteLength)

  const rootOffset = 127
  const metadataOffset = rootOffset + directory.byteLength
  const tileDataOffset = metadataOffset + metadata.byteLength

  const bytes = new Uint8Array(tileDataOffset + tile.byteLength)
  const view = new DataView(bytes.buffer)

  bytes.set(new TextEncoder().encode('PMTiles'), 0)
  bytes[7] = 3 // spec version

  // Every offset/length in the header is a little-endian uint64. JS writes them as two
  // uint32s because `setBigUint64` would need BigInt for values that are never large
  // enough to need it here — the reader reassembles exactly this pair.
  const u64 = (at: number, value: number): void => {
    view.setUint32(at, value >>> 0, true)
    view.setUint32(at + 4, Math.floor(value / 2 ** 32), true)
  }
  u64(8, rootOffset)
  u64(16, directory.byteLength)
  u64(24, metadataOffset)
  u64(32, metadata.byteLength)
  u64(40, 0) // no leaf directories
  u64(48, 0)
  u64(56, tileDataOffset)
  u64(64, tile.byteLength)
  u64(72, 1) // numAddressedTiles
  u64(80, 1) // numTileEntries
  u64(88, 1) // numTileContents

  view.setUint8(96, 1) // clustered
  view.setUint8(97, 1) // internalCompression = None
  view.setUint8(98, 1) // tileCompression = None
  view.setUint8(99, 1) // tileType = Mvt
  view.setUint8(100, 0) // minZoom
  view.setUint8(101, 0) // maxZoom
  view.setInt32(102, Math.round(bbox[0] * 1e7), true)
  view.setInt32(106, Math.round(bbox[1] * 1e7), true)
  view.setInt32(110, Math.round(bbox[2] * 1e7), true)
  view.setInt32(114, Math.round(bbox[3] * 1e7), true)
  view.setUint8(118, 0) // centerZoom
  view.setInt32(119, Math.round(((bbox[0] + bbox[2]) / 2) * 1e7), true)
  view.setInt32(123, Math.round(((bbox[1] + bbox[3]) / 2) * 1e7), true)

  bytes.set(directory, rootOffset)
  bytes.set(metadata, metadataOffset)
  bytes.set(tile, tileDataOffset)

  return { bytes, tile }
}
