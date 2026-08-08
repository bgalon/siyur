/**
 * T050 — the MapLibre `pmtiles://` protocol, pointed at OPFS.
 *
 * ADR-0002 calls this a **transport swap, not a rewrite**, and that is literally what
 * it is: `Protocol`, `PMTiles`, the directory cache and the whole style above them are
 * the DU-00 objects unchanged. The only thing that differs from `basemap.ts` is which
 * `Source` implementation the archive is read through — {@link ../opfs-source!OpfsTileSource}
 * instead of `FetchSource`.
 *
 * ## One registration for the whole app
 *
 * `maplibregl.addProtocol` **throws** on a duplicate scheme ("…is already registered"),
 * and two independent modules wanting `pmtiles://` — the dev basemap and the offline
 * bundle — is not hypothetical, it is the state of this app today. So the `Protocol`
 * instance is module-global and shared: `basemap.ts` delegates here rather than keeping
 * a second one. `Protocol` already multiplexes by archive key, which is exactly the job.
 *
 * ## The key is the whole safety property
 *
 * `Protocol.tile` parses `pmtiles://<key>/z/x/y`, looks `<key>` up in its map and — on
 * a miss — constructs `new PMTiles(<key>)`, which is a **`FetchSource` making an HTTP
 * request**. Offline that request simply fails and the map goes blank; online it
 * *succeeds*, and the app looks perfect while violating FR-018's zero-requests promise.
 * There is no error in either case. {@link bundleTileUrl} therefore derives the style
 * URL from the source's own `getKey()` so the two cannot drift apart.
 */

import maplibregl from 'maplibre-gl'
import { PMTiles, Protocol, type Source } from 'pmtiles'

/** The shared instance. `null` until something registers the scheme. */
let protocol: Protocol | null = null

/** Register `pmtiles://` with MapLibre exactly once and return the shared registry. */
export function sharedPmtilesProtocol(): Protocol {
  if (!protocol) {
    protocol = new Protocol()
    maplibregl.addProtocol('pmtiles', protocol.tile)
  }
  return protocol
}

/** Drop the registration — for tests that need a clean module state. */
export function resetPmtilesProtocol(): void {
  if (!protocol) return
  maplibregl.removeProtocol('pmtiles')
  protocol = null
}

/**
 * The MapLibre style URL for an archive registered under `key`.
 *
 * Callers pass `source.getKey()`, never a hand-written string: see the module note on
 * why a mismatch is silent and network-shaped.
 */
export function bundleTileUrl(key: string): string {
  return `pmtiles://${key}`
}

/**
 * Register `source` with the shared protocol and return the URL a style should use.
 *
 * Idempotent by construction — `Protocol.add` keys on `source.getKey()`, so registering
 * the same archive twice replaces the entry rather than duplicating it.
 */
export function registerBundleTileSource(source: Source): string {
  sharedPmtilesProtocol().add(new PMTiles(source))
  return bundleTileUrl(source.getKey())
}
