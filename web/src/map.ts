import maplibregl, {
  Map as MapLibreMap,
  type MapOptions,
  type StyleSpecification,
} from 'maplibre-gl'

/**
 * ODbL attribution. Constitution Article V requires ODbL attribution to render on
 * EVERY map, including this empty DU-00 skeleton. It stays put once real OSM/Overture
 * tile sources arrive (they add their own source attributions alongside it).
 */
export const ODBL_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors, ODbL'

/**
 * Minimal, self-contained style: version-8, no sources, a single background layer.
 * Renders a clean blank map with zero network dependency — no tile source, no glyphs,
 * no sprite. DU-01+ swaps this for the compiled bundle style (ADR-0002: HTTP first,
 * OPFS transport swap at DU-06).
 */
export const EMPTY_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#0f1720' },
    },
  ],
}

/**
 * Create the empty MapLibre map. Attribution is forced on via a custom string so the
 * ODbL notice renders even though the style carries no attributed source yet.
 */
export function createMap(
  container: HTMLElement | string,
  options: Partial<MapOptions> = {},
): MapLibreMap {
  return new maplibregl.Map({
    container,
    style: EMPTY_STYLE,
    center: [0, 0],
    zoom: 1,
    attributionControl: { customAttribution: ODBL_ATTRIBUTION },
    ...options,
  })
}
