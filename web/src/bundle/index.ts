/**
 * Public surface of the offline bundle runtime (DU-06, T048–T053).
 *
 * **Not re-exported from `src/map`, and not imported by `main.ts` statically.** The
 * same reasoning as the `basemap.ts` note in `src/map/index.ts`: this barrel reaches
 * `pmtiles`, `maplibre-gl`'s protocol registry and the OPFS worker, and a static import
 * would pull all of it into the precached app shell. Reach for it through a dynamic
 * `import('./bundle')` on the travel path.
 */

export {
  ArtifactIntegrityError,
  downloadArtifact,
  downloadBundle,
  verifyArtifact,
  type DownloadOptions,
  type DownloadProgress,
} from './download'
export {
  canonicalManifestBytes,
  manifestDigest,
  parseManifest,
  sha256Hex,
  verifyManifestDigest,
} from './manifest'
export {
  MANIFEST_PATH,
  openBundle,
  type BundleFailure,
  type OpenBundleOptions,
  type OpenBundleResult,
} from './open'
export {
  clampRange,
  type OpfsWorkerRequest,
  type OpfsWorkerResponse,
} from './opfs-protocol'
export {
  OpfsTileSource,
  WorkerRangeReader,
  createOpfsTileSource,
  type OpfsBundleLocation,
  type RangeReader,
} from './opfs-source'
export {
  bundleTileUrl,
  registerBundleTileSource,
  resetPmtilesProtocol,
  sharedPmtilesProtocol,
} from './protocol'
export {
  OpfsBundleStorage,
  readJson,
  readText,
  requestPersistentStorage,
  type BundleStorage,
} from './storage'
export {
  WITHHELD_REASONS,
  manifestArtifacts,
  type Bbox4326,
  type BundleContent,
  type BundleManifestV1,
  type BundleRouting,
  type HashedArtifact,
  type TileSourceV1,
  type WithheldEntry,
  type WithheldReason,
} from './types'
