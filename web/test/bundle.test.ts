import { PMTiles } from 'pmtiles'
import { afterEach, describe, expect, it } from 'vitest'

import {
  ArtifactIntegrityError,
  MANIFEST_PATH,
  bundleTileUrl,
  canonicalManifestBytes,
  clampRange,
  downloadArtifact,
  manifestDigest,
  openBundle,
  parseManifest,
  registerBundleTileSource,
  resetPmtilesProtocol,
  sha256Hex,
  sharedPmtilesProtocol,
  verifyArtifact,
  verifyManifestDigest,
  OpfsTileSource,
  type RangeReader,
} from '../src/bundle'
import { buildSyntheticBundle, MemoryBundleStorage } from './fixtures/bundle'
import { buildPmtiles } from './fixtures/pmtiles'

const decode = (bytes: Uint8Array): string => new TextDecoder().decode(bytes)

afterEach(() => resetPmtilesProtocol())

describe('RFC 8785 canonicalization — the rule, not the literal (ADR-0025 A5)', () => {
  /**
   * The worked example from `docs/data/bundle-manifest.md`, which is the *card's* claim
   * about what the JavaScript side must emit — an external consumer's rule, not a
   * literal lifted from this implementation. If `canonicalize` ever stopped agreeing
   * with the card, the Python writer and this verifier would diverge on the device.
   */
  it('matches the schema card’s worked JS example byte-for-byte', () => {
    const canonical = decode(
      canonicalManifestBytes({
        bbox: [28.0, 36.0, 28.5, 36.5],
        attribution: '© OpenStreetMap contributors',
      }),
    )
    expect(canonical).toBe(
      '{"attribution":"© OpenStreetMap contributors","bbox":[28,36,28.5,36.5]}',
    )
  })

  it('does NOT escape non-ASCII — the divergence that fires on every manifest', () => {
    const canonical = decode(canonicalManifestBytes({ a: '© OpenStreetMap contributors' }))
    // Python's `json.dumps` default (`ensure_ascii=True`) would emit `©` here, and
    // the resulting digest would never match. Assert the absence of the escape, not the
    // presence of the glyph — the escape is the failure mode.
    expect(canonical).not.toContain('\\u00a9')
    expect(canonical).toContain('©')
  })

  it('emits integral floats without a trailing .0 — the intermittent divergence', () => {
    // Fires only when a bbox bound lands on a whole degree, which is why the synthetic
    // bundle pins one there. `28.0` here would mismatch a Python-written manifest.
    expect(decode(canonicalManifestBytes({ bbox: [28.0, 36.44] }))).toBe('{"bbox":[28,36.44]}')
  })

  it('removes the `integrity` key entirely — not null, not empty', async () => {
    const canonical = decode(
      canonicalManifestBytes({ b: 1, integrity: { manifest_sha256: 'deadbeef' } }),
    )
    expect(canonical).not.toContain('integrity')
    expect(JSON.parse(canonical)).not.toHaveProperty('integrity')
    // Same object with a *different* integrity value must hash identically: that is the
    // property "a hash cannot cover itself" actually means.
    const a = await manifestDigest({ b: 1, integrity: { manifest_sha256: 'aaaa' } })
    const c = await manifestDigest({ b: 1, integrity: { manifest_sha256: 'bbbb' } })
    expect(a).toBe(c)
    expect(a).toBe(await manifestDigest({ b: 1 }))
  })

  it('sorts keys by code point regardless of insertion order', () => {
    expect(decode(canonicalManifestBytes({ z: 1, a: 2, M: 3 }))).toBe('{"M":3,"a":2,"z":1}')
  })

  it('digests are lowercase hex, 64 characters', async () => {
    expect(await sha256Hex(new TextEncoder().encode('x'))).toMatch(/^[0-9a-f]{64}$/)
  })
})

describe('the synthetic bundle verifies against its own manifest', () => {
  it('verifies, and keeps verifying after a re-parse', async () => {
    const { raw, manifest } = await buildSyntheticBundle()
    expect(await verifyManifestDigest(raw)).toBe(true)
    expect(manifest.schema_ver).toBe('BundleManifestV1')
    // `tiles.pmtiles` survives as a WHOLE TileSourceV1, not a summary of one.
    expect(manifest.tiles.pmtiles.tile_license).toBe('ODbL-1.0')
    expect(manifest.tiles.pmtiles.style?.path).toBe('style/base.json')
    expect(manifest.textLicense).toBe('CC-BY-SA-4.0')
  })

  it('the digest is taken over the RAW manifest, so an unmodelled field still verifies', async () => {
    // An M2 bundle carries `schematic`, which this M1 client does not model. Hashing the
    // *narrowed* object would drop it and fail the check as if the bundle were corrupt.
    const { raw } = await buildSyntheticBundle((m) => {
      m.schematic = { style_json: 'schematic/style.json', sha256: 'abc' }
    })
    expect(raw).toHaveProperty('schematic')
    expect(parseManifest(raw)).not.toBeNull()
    expect(await verifyManifestDigest(raw)).toBe(true)
  })

  it('MUTATION GUARD: changing any manifest field breaks the digest', async () => {
    const { raw } = await buildSyntheticBundle()
    const tampered = structuredClone(raw) as Record<string, unknown>
    ;(tampered.content as Record<string, string>).sites = 'content/other.json'
    expect(await verifyManifestDigest(tampered)).toBe(false)
  })

  it('a manifest with no integrity block never verifies', async () => {
    const { raw } = await buildSyntheticBundle()
    const stripped = structuredClone(raw) as Record<string, unknown>
    delete stripped.integrity
    expect(await verifyManifestDigest(stripped)).toBe(false)
  })
})

describe('parseManifest fails closed', () => {
  const cases: [string, (m: Record<string, unknown>) => void][] = [
    ['a wrong schema_ver', (m) => void (m.schema_ver = 'BundleManifestV2')],
    ['a missing content block', (m) => void delete m.content],
    ['a missing routing hash', (m) => void delete (m.routing as Record<string, unknown>).legs_sha256],
    ['a tile source with no bbox', (m) => void delete (m.tiles as { pmtiles: Record<string, unknown> }).pmtiles.bbox],
    [
      'a bbox bound outside EPSG:4326',
      (m) => void ((m.tiles as { pmtiles: Record<string, unknown> }).pmtiles.bbox = [28.0, 36.44, 28.232, 191.4]),
    ],
    [
      'a mis-ordered bbox (west east of east)',
      (m) => void ((m.tiles as { pmtiles: Record<string, unknown> }).pmtiles.bbox = [28.3, 36.44, 28.2, 36.451]),
    ],
    ['a missing attribution artifact', (m) => void delete m.attribution],
  ]

  it.each(cases)('returns null for %s', async (_label, mutate) => {
    const { raw } = await buildSyntheticBundle()
    const broken = structuredClone(raw) as Record<string, unknown>
    mutate(broken)
    expect(parseManifest(broken)).toBeNull()
  })

  it('does NOT claim to detect a lon/lat swap — both bounds are in range', async () => {
    // Recorded as a limit, not a gap in the tests. `[36.44, 28.0, 36.451, 28.232]` is a
    // swapped Rhodes bbox and is a perfectly well-formed EPSG:4326 extent off the coast
    // of Somalia. Nothing in a manifest distinguishes them; the defence is that
    // coordinates are computed in PostGIS/shapely, never emitted by a model.
    const { raw } = await buildSyntheticBundle((m) => {
      ;(m.tiles as { pmtiles: Record<string, unknown> }).pmtiles.bbox = [36.44, 28.0, 36.451, 28.232]
    })
    expect(parseManifest(raw)).not.toBeNull()
  })

  it('drops a withheld entry whose reason is outside the closed enum', async () => {
    const { raw } = await buildSyntheticBundle((m) => {
      m.withheld = [
        { site_id: 'a', field: 'reviews', reason: 'because the vendor said so' },
        { site_id: 'a', field: 'notes[2]', reason: 'source_unavailable' },
      ]
    })
    const manifest = parseManifest(raw)
    expect(manifest?.withheld).toHaveLength(1)
    expect(manifest?.withheld[0]?.reason).toBe('source_unavailable')
  })

  it('accepts a null textLicense — the correct value for a bundle with no narration', async () => {
    const { raw } = await buildSyntheticBundle((m) => void (m.textLicense = null))
    expect(parseManifest(raw)?.textLicense).toBeNull()
  })
})

describe('openBundle — the launch-time integrity check (T053, FR-020)', () => {
  it('opens a complete, intact bundle', async () => {
    const { storage } = await buildSyntheticBundle()
    const result = await openBundle({ storage })
    expect(result.ok).toBe(true)
  })

  it('reports a missing manifest apart from an unreadable one', async () => {
    const empty = new MemoryBundleStorage()
    expect(await openBundle({ storage: empty })).toMatchObject({
      ok: false,
      failure: 'manifest_missing',
    })

    const { storage } = await buildSyntheticBundle()
    await storage.writeAt(MANIFEST_PATH, 0, new TextEncoder().encode('{ truncated'))
    await storage.remove(MANIFEST_PATH)
    await storage.writeAt(MANIFEST_PATH, 0, new TextEncoder().encode('{ truncated'))
    expect(await openBundle({ storage })).toMatchObject({
      ok: false,
      failure: 'manifest_unreadable',
    })
  })

  it('reports a tampered manifest as a digest mismatch, never as a partial day', async () => {
    const { storage, raw } = await buildSyntheticBundle()
    const tampered = structuredClone(raw) as Record<string, unknown>
    tampered.size_bytes = 1
    await storage.remove(MANIFEST_PATH)
    await storage.writeAt(MANIFEST_PATH, 0, new TextEncoder().encode(JSON.stringify(tampered)))
    expect(await openBundle({ storage })).toMatchObject({
      ok: false,
      failure: 'manifest_digest_mismatch',
    })
  })

  it('reports an evicted artifact by name rather than rendering what survived', async () => {
    const { storage, manifest } = await buildSyntheticBundle()
    await storage.remove(manifest.content.narrations)
    expect(await openBundle({ storage })).toMatchObject({
      ok: false,
      failure: 'artifact_missing',
      detail: 'content/narrations.json',
    })
  })

  it('reports a corrupted content artifact by name (one hash per artifact)', async () => {
    const { storage, manifest } = await buildSyntheticBundle()
    await storage.writeAt(manifest.content.sites, 4, new Uint8Array([0x21]))
    expect(await openBundle({ storage })).toMatchObject({
      ok: false,
      failure: 'artifact_corrupt',
      detail: 'content/sites.json',
    })
  })

  it('catches a truncated archive by its magic without hashing 200 MB', async () => {
    const { storage, manifest } = await buildSyntheticBundle()
    await storage.remove(manifest.tiles.pmtiles.path)
    await storage.writeAt(manifest.tiles.pmtiles.path, 0, new Uint8Array(200))
    expect(await openBundle({ storage })).toMatchObject({
      ok: false,
      failure: 'artifact_corrupt',
      detail: manifest.tiles.pmtiles.path,
    })
  })

  it('a corrupted archive INTERIOR passes the shallow check and fails the deep one', async () => {
    // This asserts the documented limit of the default, so the trade-off in `open.ts`
    // is a tested claim rather than a comment. A byte flipped in the tile data leaves
    // the header intact.
    const { storage, manifest } = await buildSyntheticBundle()
    const path = manifest.tiles.pmtiles.path
    const bytes = (await storage.read(path)) as Uint8Array
    await storage.writeAt(path, bytes.byteLength - 1, new Uint8Array([0xff]))
    expect(await openBundle({ storage })).toMatchObject({ ok: true })
    expect(await openBundle({ storage, deep: true })).toMatchObject({
      ok: false,
      failure: 'artifact_corrupt',
      detail: path,
    })
  })
})

describe('the download manager resumes by range (T048)', () => {
  const body = new TextEncoder().encode('0123456789abcdefghij')

  const rangeServer = (
    seen: { range: string | null }[],
    behaviour: 'range' | 'ignores-range' | '416' = 'range',
  ): typeof fetch =>
    (async (_url: string, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>
      const range = headers.Range ?? null
      seen.push({ range })
      if (behaviour === '416' && range) {
        return new Response(null, { status: 416 })
      }
      if (behaviour === 'ignores-range' || !range) {
        return new Response(body, { status: 200 })
      }
      const start = Number(/bytes=(\d+)-/.exec(range)?.[1] ?? 0)
      return new Response(body.slice(start), { status: 206 })
    }) as unknown as typeof fetch

  it('asks for only the missing tail and writes it at the right offset', async () => {
    const storage = new MemoryBundleStorage()
    await storage.writeAt('a.bin', 0, body.slice(0, 8))
    const seen: { range: string | null }[] = []

    const written = await downloadArtifact('a.bin', {
      baseUrl: 'https://example.test/bundle/',
      storage,
      fetchImpl: rangeServer(seen),
    })

    expect(seen[0]?.range).toBe('bytes=8-')
    expect(written).toBe(body.byteLength - 8)
    expect(decode((await storage.read('a.bin')) as Uint8Array)).toBe(decode(body))
  })

  it('discards the partial when the server ignores Range and restarts at 200', async () => {
    // **The partial is deliberately LONGER than the object**, which is the only case
    // where the discard is observable — and therefore the only honest way to test it.
    // A same-length partial is overwritten byte-for-byte whether or not the file is
    // removed first, so the obvious version of this test passes with the guard deleted
    // (verified by mutation). Writes are position-addressed and OPFS's
    // `keepExistingData: true` does not truncate, so without the discard the stale tail
    // survives past the end of the real object and the artifact hashes wrong — silently,
    // and only at launch.
    const storage = new MemoryBundleStorage()
    const stalePartial = new Uint8Array(body.byteLength + 6).fill(0x5a)
    await storage.writeAt('a.bin', 0, stalePartial)

    await downloadArtifact('a.bin', {
      baseUrl: 'https://example.test/bundle/',
      storage,
      fetchImpl: rangeServer([], 'ignores-range'),
    })

    const stored = (await storage.read('a.bin')) as Uint8Array
    expect(stored.byteLength).toBe(body.byteLength)
    expect(decode(stored)).toBe(decode(body))
    expect(await sha256Hex(stored)).toBe(await sha256Hex(body))
  })

  it('treats 416 on a resumed request as "already complete"', async () => {
    const storage = new MemoryBundleStorage()
    await storage.writeAt('a.bin', 0, body)
    const written = await downloadArtifact('a.bin', {
      baseUrl: 'https://example.test/bundle/',
      storage,
      fetchImpl: rangeServer([], '416'),
    })
    expect(written).toBe(0)
    expect(decode((await storage.read('a.bin')) as Uint8Array)).toBe(decode(body))
  })

  it('deletes an artifact whose hash does not match, so a resume cannot build on it', async () => {
    const storage = new MemoryBundleStorage()
    await storage.writeAt('a.bin', 0, body)
    await expect(verifyArtifact(storage, 'a.bin', 'f'.repeat(64))).rejects.toBeInstanceOf(
      ArtifactIntegrityError,
    )
    expect(await storage.size('a.bin')).toBe(0)
  })

  it('accepts an artifact whose hash does match', async () => {
    const storage = new MemoryBundleStorage()
    await storage.writeAt('a.bin', 0, body)
    await expect(verifyArtifact(storage, 'a.bin', await sha256Hex(body))).resolves.toBeUndefined()
    expect(await storage.size('a.bin')).toBe(body.byteLength)
  })
})

/**
 * A {@link RangeReader} over an in-memory archive — the worker's role, without a worker.
 *
 * It calls the **same** {@link clampRange} the worker calls, rather than restating the
 * clamp: a test double that reimplements the logic under test proves the double works.
 */
class ArrayRangeReader implements RangeReader {
  reads = 0
  constructor(private readonly bytes: Uint8Array) {}
  async open(): Promise<number> {
    return this.bytes.byteLength
  }
  async getBytes(offset: number, length: number): Promise<ArrayBuffer> {
    this.reads++
    const range = clampRange(this.bytes.byteLength, offset, length)
    return this.bytes.slice(range.start, range.start + range.length).buffer as ArrayBuffer
  }
  async close(): Promise<void> {}
}

describe('clampRange — the worker’s short-read rule, extracted so it can be tested', () => {
  // `opfs-worker.ts` cannot be unit-tested (jsdom has no Worker and no OPFS), so this
  // rule is pulled out into a pure function rather than left to the e2e alone.
  it('never returns more bytes than the file holds', () => {
    // The exact call `pmtiles` makes when it opens an archive smaller than 16 KiB.
    expect(clampRange(300, 0, 16384)).toEqual({ start: 0, length: 300 })
  })

  it('returns nothing at all for a range wholly past EOF', () => {
    expect(clampRange(300, 400, 100)).toEqual({ start: 300, length: 0 })
  })

  it('truncates a range that straddles EOF', () => {
    expect(clampRange(300, 250, 100)).toEqual({ start: 250, length: 50 })
  })

  it('passes a fully in-bounds range through unchanged', () => {
    expect(clampRange(300, 100, 50)).toEqual({ start: 100, length: 50 })
  })

  it('clamps a negative offset rather than reading backwards', () => {
    expect(clampRange(300, -10, 50)).toEqual({ start: 0, length: 50 })
  })
})

describe('OpfsTileSource feeds a real pmtiles reader (T049/T050)', () => {
  it('reads a tile out of the archive through the Source interface', async () => {
    const archive = buildPmtiles()
    const source = new OpfsTileSource(new ArrayRangeReader(archive.bytes), 'opfs://b/tiles/a.pmtiles')
    const reader = new PMTiles(source)

    const header = await reader.getHeader()
    expect(header.maxZoom).toBe(0)
    expect(header.minLon).toBeCloseTo(28.216, 3)

    const tile = await reader.getZxy(0, 0, 0)
    expect(new Uint8Array(tile?.data as ArrayBuffer)).toEqual(archive.tile)
  })

  it('opens the archive exactly once under concurrent reads', async () => {
    // Two `open()` calls would race for the sync-access lock and the second would throw
    // `NoModificationAllowedError` in a real worker.
    const archive = buildPmtiles()
    let opens = 0
    const reader: RangeReader = {
      open: async () => {
        opens++
        return archive.bytes.byteLength
      },
      getBytes: async (offset, length) =>
        archive.bytes.slice(offset, offset + length).buffer as ArrayBuffer,
      close: async () => {},
    }
    const source = new OpfsTileSource(reader, 'opfs://b/tiles/a.pmtiles')
    await Promise.all([source.getBytes(0, 16), source.getBytes(0, 16), source.getBytes(16, 16)])
    expect(opens).toBe(1)
  })

  it('a short read at EOF returns only the bytes that exist', async () => {
    const archive = buildPmtiles()
    const source = new OpfsTileSource(new ArrayRangeReader(archive.bytes), 'k')
    const { data } = await source.getBytes(0, 16384)
    expect(data.byteLength).toBe(archive.bytes.byteLength)
  })
})

describe('the protocol key is the network-leak guard (T050)', () => {
  it('registers under the source’s own key, and the style URL is derived from it', () => {
    const source = new OpfsTileSource(
      new ArrayRangeReader(buildPmtiles().bytes),
      'opfs://bundles/bnd_01JTEST/tiles/area.pmtiles',
    )
    const url = registerBundleTileSource(source)

    expect(url).toBe(`pmtiles://${source.getKey()}`)
    // The property that matters: the key MapLibre will parse out of the tile URL is
    // present in the registry. On a miss `Protocol` silently builds a `FetchSource` and
    // goes to the network — no error, no blank map, just a live request on the one
    // surface whose promise is that there is none.
    const key = /^pmtiles:\/\/(.+)$/.exec(`${url}/0/0/0`.replace(/\/0\/0\/0$/, ''))?.[1]
    expect(sharedPmtilesProtocol().get(key as string)).toBeDefined()
  })

  it('NEGATIVE CONTROL: a key that does not match is NOT in the registry', () => {
    // Without this, the assertion above would pass against a registry that returned
    // something for every string.
    const source = new OpfsTileSource(new ArrayRangeReader(buildPmtiles().bytes), 'opfs://a')
    registerBundleTileSource(source)
    expect(sharedPmtilesProtocol().get('opfs://a')).toBeDefined()
    expect(sharedPmtilesProtocol().get('opfs://a/tiles/area.pmtiles')).toBeUndefined()
    expect(sharedPmtilesProtocol().get('https://example.test/a.pmtiles')).toBeUndefined()
  })

  it('the OPFS store and the tile source agree on where the archive is', async () => {
    // The one coupling a test can catch before a worker throws `NotFoundError` inside a
    // context with no useful stack. `OpfsBundleStorage` supplies both halves, so the
    // download manager's directory and the worker's directory are the same by
    // construction rather than by two callers typing the same strings.
    const { OpfsBundleStorage } = await import('../src/bundle/storage')
    const store = new OpfsBundleStorage('bnd_01JTEST')
    expect(store.rootDirs).toEqual(['bundles', 'bnd_01JTEST'])
    expect(store.key).toBe(`opfs://${store.rootDirs.join('/')}`)
    // …and the protocol key a tile source would take is the store key plus the
    // manifest-relative path, so `pmtiles://<key>` addresses this exact file.
    expect(`${store.key}/tiles/area.pmtiles`).toBe(
      'opfs://bundles/bnd_01JTEST/tiles/area.pmtiles',
    )
  })

  it('bundleTileUrl round-trips the key MapLibre’s own regex extracts', () => {
    // Assert against `Protocol`'s parsing rule, not against our own formatting: the
    // library matches `pmtiles://(.+)/z/x/y`, so the key must survive that greedy split.
    const key = 'opfs://bundles/bnd_01JTEST/tiles/area.pmtiles'
    const tileUrl = `${bundleTileUrl(key)}/14/9700/6200`
    expect(/pmtiles:\/\/(.+)\/(\d+)\/(\d+)\/(\d+)/.exec(tileUrl)?.[1]).toBe(key)
  })
})
