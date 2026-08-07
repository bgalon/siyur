/**
 * The vector basemap — MapLibre + PMTiles, per `docs/data/tile-source.md`.
 *
 * **Status: dev affordance, not DU-05.** The schema card puts the real tile source
 * inside the compiled bundle (`TileSourceV1` in `BundleManifestV1`) and lands the
 * Protomaps ADR at DU-05. None of that exists yet. What this module provides is the
 * *client half* of that design — the `pmtiles://` protocol, the Protomaps style, the
 * vendored glyphs — pointed at a plain HTTP archive instead of a bundle. DU-05 swaps
 * the archive URL for the bundle's `tiles.pmtiles` and keeps everything below intact;
 * ADR-0002's "HTTP first, OPFS transport swap later" is exactly this shape.
 *
 * The production default is still {@link ../map!EMPTY_STYLE}. `main.ts` opts in only
 * under `import.meta.env.DEV`, so no basemap reaches a production bundle from here.
 *
 * **Nothing here is bound to a place.** The archive URL and the label language are
 * parameters; a different `.pmtiles` over a different city renders with no code
 * change (FR-001). The Rhodes extract shipped in `public/tiles/` is a demo fixture,
 * the same way Rhodes is the demo default everywhere else.
 *
 * **Licence.** Tile *data* is © OpenStreetMap contributors under ODbL — already
 * rendered unconditionally by {@link ../attribution!OdblAttributionControl}, which
 * `attribution.ts` anticipated ("it stays put once real OSM/Overture tile sources
 * arrive"). The style/pipeline is BSD-3-Clause, the Noto glyphs OFL-1.1, the sprites
 * MIT. All are in the bundleable set; none needs a new credit surface.
 */

import { layers, namedFlavor } from '@protomaps/basemaps'
import maplibregl, { type StyleSpecification } from 'maplibre-gl'
import { Protocol } from 'pmtiles'

import { ODBL_ATTRIBUTION } from './attribution'

/** Style-internal id of the vector source. Referenced by every Protomaps layer. */
export const BASEMAP_SOURCE_ID = 'protomaps'

/**
 * Where the vendored font ranges live, relative to the web root.
 *
 * **Only U+0000–U+04FF is vendored** (ranges `0-255` … `1024-1279`): Latin, Latin
 * Extended, IPA, Greek and Cyrillic. That covers the Rhodes demo and most of Europe
 * and is emphatically *not* the any-area claim FR-001 makes — a Hebrew, Arabic, or
 * CJK label has no glyphs here and MapLibre renders it as nothing at all, silently.
 * Pruning glyph ranges to what a style actually references is DU-05's job (the
 * schema card calls this "a classic offline footgun"); until then, treat missing
 * text outside these ranges as a known gap, not a rendering bug.
 */
export const GLYPHS_URL = '/basemap/glyphs/{fontstack}/{range}.pbf'

/** Vendored sprite sheet (MapLibre appends `.json` / `.png` and the `@2x` variants). */
export const SPRITE_URL_BASE = '/basemap/sprites'

/**
 * Resolve a root-relative asset path against the current origin.
 *
 * **MapLibre requires `style.sprite` to be absolute** and throws
 * `Invalid sprite URL "…", must be absolute` during `_loadSprite` otherwise — so a
 * root-relative path that every other part of the app accepts is rejected here, and the
 * sprite sheet never loads. `glyphs` has no such rule (it is a URL *template*, resolved
 * per-fontstack), which is why only this one needs the treatment and why the asymmetry
 * looks arbitrary until you hit it.
 *
 * The failure is quiet in the worst way: the style still loads, tiles still draw, and
 * only sprite-backed icons are missing — so the map looks fine at a glance and the error
 * lives in the console (FAIL-007).
 *
 * Falls back to the relative path when there is no `location` (non-browser import), where
 * a sprite is never fetched anyway.
 */
export function absoluteAssetUrl(path: string): string {
  if (typeof location === 'undefined') return path
  return new URL(path, location.origin).href
}

/** Protomaps flavors we expose. `dark` matches the app's own surface. */
export type BasemapFlavor = 'light' | 'dark' | 'white' | 'black' | 'grayscale'

export interface BasemapStyleOptions {
  /** URL of the PMTiles archive, e.g. `/tiles/rhodes.pmtiles`. */
  archiveUrl: string
  /** Protomaps flavor. Defaults to `dark`, matching `EMPTY_STYLE`'s background. */
  flavor?: BasemapFlavor
  /**
   * Label language, as a BCP-47 primary subtag. Defaults to `en`; the Protomaps
   * schema falls back to the local `name` when a translation is absent, which is
   * the behaviour we want — a place's own name is the honest default.
   */
  lang?: string
}

/**
 * MapLibre resolves `pmtiles://` URLs through a registered protocol handler, and
 * registration is global to the module rather than per-map. Registering twice throws,
 * so this is idempotent — `basemapStyle` may be called once per map, or in a test
 * that builds several.
 */
let protocol: Protocol | null = null

/**
 * Teach MapLibre to read `pmtiles://` URLs. Safe to call repeatedly.
 *
 * Exported because the protocol must be registered **before** a map with a PMTiles
 * source is constructed; {@link basemapStyle} does it for you, and a caller building
 * a style by hand can do it explicitly.
 */
export function registerPmtilesProtocol(): void {
  if (protocol) return
  protocol = new Protocol()
  maplibregl.addProtocol('pmtiles', protocol.tile)
}

/** Drop the protocol registration — for tests that need a clean module state. */
export function unregisterPmtilesProtocol(): void {
  if (!protocol) return
  maplibregl.removeProtocol('pmtiles')
  protocol = null
}

/**
 * A complete MapLibre style rendering `archiveUrl` with the Protomaps basemap layers.
 *
 * The source carries `attribution` so MapLibre's own accounting knows the tiles are
 * ODbL even though the visible credit comes from our control — the two must not
 * disagree about what the basemap owes.
 */
export function basemapStyle(options: BasemapStyleOptions): StyleSpecification {
  const { archiveUrl, flavor = 'dark', lang = 'en' } = options
  registerPmtilesProtocol()

  return {
    version: 8,
    glyphs: GLYPHS_URL,
    sprite: absoluteAssetUrl(
      `${SPRITE_URL_BASE}/${flavor === 'dark' || flavor === 'black' ? 'dark' : 'light'}`,
    ),
    sources: {
      [BASEMAP_SOURCE_ID]: {
        type: 'vector',
        url: `pmtiles://${archiveUrl}`,
        attribution: ODBL_ATTRIBUTION,
      },
    },
    layers: layers(BASEMAP_SOURCE_ID, namedFlavor(flavor), { lang }),
  }
}
