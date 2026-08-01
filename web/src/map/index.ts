/**
 * Public surface of the map layer. `src/map.ts` (DU-00) became `src/map/map.ts`
 * when the sites + attribution modules landed; importing `./map` still resolves
 * here, so the DU-00 empty-map behaviour and its import path are unchanged.
 */

export { EMPTY_STYLE, ODBL_ATTRIBUTION, createMap, createMapWithAttribution } from './map'
export { OSM_ATTRIBUTION, OdblAttributionControl, isOdblAttribution } from './attribution'
export {
  chipTextFromSource,
  createAttributionChip,
  renderSourcedValue,
  type RenderSourcedValueOptions,
} from './attribution-chip'
export {
  DEFAULT_SITES_ENDPOINT,
  SitesLayer,
  SitesRequestError,
  buildMarkerElement,
  buildPopupContent,
  createSiteMarker,
  displayName,
  displayNameOrder,
  displayNameTag,
  fetchSites,
  formatBbox,
  mountSitesLayer,
  type BboxSource,
  type FetchSitesOptions,
  type SitesLayerOptions,
} from './sites'
export {
  isGeoPoint,
  isSourceRef,
  isStamped,
  isStampedLocation,
  isStampedString,
  sanitiseSite,
  sanitiseSitesResponse,
} from './guards'
export type { GeoPoint, SiteRecordV1, SitesResponse, SourceRef, SourcedValue } from './types'
