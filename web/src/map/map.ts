import maplibregl, {
  Map as MapLibreMap,
  type MapOptions,
  type StyleSpecification,
} from 'maplibre-gl'

import { ODBL_ATTRIBUTION, OdblAttributionControl } from './attribution'

// The ODbL notice now lives with the control that renders it (`attribution.ts`),
// re-exported here so the DU-00 import path (`src/map`) is unchanged.
export { ODBL_ATTRIBUTION } from './attribution'

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

/**
 * Create the map with the data-driven attribution control (T044) instead of the
 * built-in one, so there is exactly one attribution line. The ODbL notice still
 * renders unconditionally; `/sites` responses add their required credits on top.
 */
export function createMapWithAttribution(
  container: HTMLElement | string,
  options: Partial<MapOptions> = {},
): { map: MapLibreMap; attribution: OdblAttributionControl } {
  const map = createMap(container, { attributionControl: false, ...options })
  const attribution = new OdblAttributionControl()
  map.addControl(attribution, 'bottom-right')
  return { map, attribution }
}
