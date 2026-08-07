/**
 * The dev basemap style (`src/map/basemap.ts`).
 *
 * What is worth asserting here is not "MapLibre renders tiles" — that is MapLibre's
 * test suite — but the three properties this module is responsible for and that a
 * later refactor could silently break: the archive is addressed through the
 * `pmtiles://` protocol, the ODbL credit travels with the source, and nothing about
 * the style is bound to a particular place.
 */

import { afterEach, describe, expect, it } from 'vitest'

import { ODBL_ATTRIBUTION } from '../src/map/attribution'
import {
  BASEMAP_SOURCE_ID,
  GLYPHS_URL,
  basemapStyle,
  unregisterPmtilesProtocol,
} from '../src/map/basemap'

const RHODES = '/tiles/rhodes.pmtiles'

/** The style's vector source, narrowed — the spec type is a wide union. */
const vectorSource = (
  style: ReturnType<typeof basemapStyle>,
): { type: string; url?: string; attribution?: string } =>
  style.sources[BASEMAP_SOURCE_ID] as { type: string; url?: string; attribution?: string }

afterEach(() => {
  unregisterPmtilesProtocol()
})

describe('the PMTiles archive is addressed through the pmtiles protocol', () => {
  it('prefixes the archive URL with the protocol MapLibre resolves', () => {
    const source = vectorSource(basemapStyle({ archiveUrl: RHODES }))
    expect(source.type).toBe('vector')
    // Not a bare HTTP url: without the scheme MapLibre would fetch the archive as
    // if it were a TileJSON document and the map would render empty.
    expect(source.url).toBe(`pmtiles://${RHODES}`)
  })

  it('registers the protocol without throwing when several styles are built', () => {
    // `addProtocol` throws on a duplicate registration, so a second map on the same
    // page would fail if this were not idempotent.
    expect(() => {
      basemapStyle({ archiveUrl: RHODES })
      basemapStyle({ archiveUrl: '/tiles/other.pmtiles' })
    }).not.toThrow()
  })
})

describe('the basemap carries its own licence obligation', () => {
  it('stamps the source with the ODbL credit', () => {
    // The visible credit comes from OdblAttributionControl; this keeps MapLibre's
    // own attribution accounting from disagreeing about what the tiles owe.
    expect(vectorSource(basemapStyle({ archiveUrl: RHODES })).attribution).toBe(
      ODBL_ATTRIBUTION,
    )
  })
})

describe('the style is place-neutral (FR-001)', () => {
  it('renders any archive through the same call, with no place in the style', () => {
    const elsewhere = basemapStyle({ archiveUrl: '/tiles/kyoto.pmtiles' })
    expect(vectorSource(elsewhere).url).toBe('pmtiles:///tiles/kyoto.pmtiles')
    // Nothing Rhodes-shaped leaks in from a default.
    expect(JSON.stringify(elsewhere)).not.toMatch(/rhodes/i)
  })

  it('takes the label language as a parameter rather than assuming one', () => {
    const el = basemapStyle({ archiveUrl: RHODES, lang: 'el' })
    const en = basemapStyle({ archiveUrl: RHODES, lang: 'en' })
    // The language reaches the layers — the two styles must not be identical.
    expect(JSON.stringify(el)).not.toBe(JSON.stringify(en))
  })

  it('points glyphs at the vendored ranges, not a remote host', () => {
    // A remote glyph URL would break the offline posture ADR-0002 sets up, and it
    // is the kind of thing that works in dev and fails only in airplane mode.
    expect(basemapStyle({ archiveUrl: RHODES }).glyphs).toBe(GLYPHS_URL)
    expect(GLYPHS_URL.startsWith('/')).toBe(true)
  })

  it('serves sprites from the vendored path too', () => {
    expect(basemapStyle({ archiveUrl: RHODES }).sprite).toMatch(/\/basemap\/sprites\//)
  })

  // FAIL-007. These assertions previously pinned `sprite` to the ROOT-RELATIVE path, so
  // they passed green while MapLibre threw `Invalid sprite URL … must be absolute` at
  // every real page load and no sprite ever rendered. The test encoded the bug. It now
  // asserts the property MapLibre actually requires — absolute — rather than the literal
  // string the implementation happened to produce.
  it('makes the sprite URL ABSOLUTE, which MapLibre requires (FAIL-007)', () => {
    const { sprite } = basemapStyle({ archiveUrl: RHODES })
    expect(sprite).toBeDefined()
    expect(() => new URL(sprite as string)).not.toThrow()
    expect(sprite as string).toMatch(/^https?:\/\//)
  })

  it('still points at the vendored origin, not a remote host', () => {
    const sprite = basemapStyle({ archiveUrl: RHODES }).sprite as string
    expect(new URL(sprite).origin).toBe(location.origin)
  })
})

describe('flavors', () => {
  it('defaults to the dark sprite sheet, matching the app surface', () => {
    expect(basemapStyle({ archiveUrl: RHODES }).sprite).toBe(
      `${location.origin}/basemap/sprites/dark`,
    )
  })

  it('uses the light sheet for a light flavor', () => {
    expect(basemapStyle({ archiveUrl: RHODES, flavor: 'light' }).sprite).toBe(
      `${location.origin}/basemap/sprites/light`,
    )
  })

  it('produces a non-empty layer list', () => {
    expect(basemapStyle({ archiveUrl: RHODES }).layers.length).toBeGreaterThan(0)
  })
})
