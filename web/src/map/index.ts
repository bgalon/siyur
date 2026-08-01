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
  AreaRequestError,
  AreaResponseError,
  AreaReuseSurface,
  DEFAULT_AREAS_ENDPOINT,
  buildCoverageCard,
  coverageAction,
  describeStaleness,
  researchPath,
  resolveAndApply,
  resolveArea,
  type AreaReuseOptions,
  type AreaRequest,
  type CoverageAction,
  type CoverageCardOptions,
  type ResearchRequest,
  type ResolveAreaOptions,
} from './areas'
export {
  DelimitControl,
  isUsableBbox,
  mountDelimitControl,
  viewportBbox,
  type Bbox,
  type DelimitControlOptions,
} from './delimit'
export {
  ResearchProgressSurface,
  ResearchRequestError,
  ResearchStreamError,
  SseFrameParser,
  describeStatus,
  mountResearchProgress,
  runResearch,
  streamResearch,
  type ResearchHandlers,
  type ResearchProgressOptions,
  type ResearchState,
  type SseFrame,
  type StreamResearchOptions,
} from './research'
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
  mountAreaReuse,
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
  sanitiseAreaResolution,
  sanitiseCoverage,
  sanitisePolygon,
  sanitiseResearchDone,
  sanitiseResearchStatus,
  sanitiseResearchSummary,
  sanitiseSite,
  sanitiseSitesResponse,
} from './guards'
export type {
  AreaCoverage,
  AreaResolution,
  GeoPoint,
  GeoPolygon,
  ResearchStatus,
  ResearchSummary,
  SiteRecordV1,
  SitesResponse,
  SourceRef,
  SourcedValue,
} from './types'
