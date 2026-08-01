/**
 * Wire types for `GET /sites` and `POST /areas` — the shapes defined by
 * `specs/001-research-cited-sites/contracts/sites.md`, `contracts/areas.md` and
 * the `SiteRecordV1` schema card (`docs/data/poi-site.md`).
 *
 * These describe data that has ALREADY been validated by {@link ../guards}.
 * Anything straight off `fetch()` is `unknown` until a guard narrows it — the
 * client must never assume a value carries a provenance stamp (FR-003).
 */

/** Where a value came from. `docs/data/poi-site.md` § SourceRef. */
export interface SourceRef {
  /** `overture` | `osm` | `wikivoyage` | `wikipedia` | `wikidata` | `commons` | … */
  readonly kind: string
  /** GERS id / OSM `type/id` / QID / article title / URL. */
  readonly id?: string | null
  readonly url?: string | null
  /** SPDX string, `"proprietary"` or `"user-owned"`. Drives `bundleable`. */
  readonly license: string
  /** The exact string to render when the license requires credit (ODbL, CC BY-SA). */
  readonly attribution?: string | null
}

/**
 * The atomic unit: a fact plus its stamp. Everything Siyur shows is a
 * `SourcedValue`, never a bare fact (Constitution Article V).
 */
export interface SourcedValue<T> {
  readonly value: T
  readonly source: SourceRef
  readonly bundleable?: boolean
  readonly confidence?: number
  readonly observed_at?: string
}

/** GeoJSON Point in EPSG:4326 — `[lon, lat]`. */
export interface GeoPoint {
  readonly type: 'Point'
  readonly coordinates: readonly [number, number]
}

/**
 * A commons record. Keys of `names` are **BCP-47 subtags** (`en`, `el`,
 * `el-Latn`), not bare language codes.
 */
export interface SiteRecordV1 {
  readonly id: string
  readonly gers_id?: string | null
  readonly schema_ver?: string
  readonly names: Readonly<Record<string, SourcedValue<string>>>
  readonly location: SourcedValue<GeoPoint>
  readonly categories?: readonly SourcedValue<string>[]
  readonly address?: SourcedValue<string> | null
  readonly opening_hours?: SourcedValue<string> | null
  readonly conflicts?: readonly unknown[]
  readonly updated_at?: string
}

/** `200` body of `GET /sites?bbox=…`. */
export interface SitesResponse {
  readonly sites: readonly SiteRecordV1[]
  /** Union of required attribution strings across every returned value. */
  readonly attribution: readonly string[]
}

/* --------------------------------------------------- POST /areas (US2) ----- */

/** GeoJSON Polygon in EPSG:4326 — rings of `[lon, lat]`. */
export interface GeoPolygon {
  readonly type: 'Polygon'
  readonly coordinates: readonly (readonly (readonly [number, number])[])[]
}

/**
 * `coverage` block of the `POST /areas` `200` body (`contracts/areas.md`).
 * Drives the reuse/refresh decision (FR-006 / US2 / SC-003).
 */
export interface AreaCoverage {
  /** `ST_Within(geom, polygon)` count of commons records already in the area. */
  readonly known_site_count: number
  /** The server's own verdict. `true` ⇒ show existing data, do NOT research. */
  readonly covered: boolean
  /** Min `observed_at` among covered records — how stale the data may be. */
  readonly stalest_observed_at: string | null
  /** Always `true` when covered: a refresh is offered, never auto-run. */
  readonly refresh_available: boolean
}

/** `200` body of `POST /areas` — a resolved area plus its commons coverage. */
export interface AreaResolution {
  readonly area_id: string
  readonly polygon: GeoPolygon | null
  readonly coverage: AreaCoverage
}
