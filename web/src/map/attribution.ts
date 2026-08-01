/**
 * T044 — the map-level attribution control (FR-004 / SC-002 / Constitution Article V).
 *
 * DU-00 shipped a hard-wired ODbL notice on the built-in MapLibre attribution
 * control. This extends it rather than replacing it: the ODbL credit is still
 * rendered unconditionally on every map, and on top of that the control now
 * mirrors the **`attribution[]` array of the `GET /sites` response**. The client
 * does not scan site sources and decide what credit is owed — the server sends
 * the union of required strings and we render exactly those, deduped.
 */

import type { IControl, Map as MapLibreMap } from 'maplibre-gl'
import type { SitesResponse } from './types'

/**
 * ODbL attribution. Constitution Article V requires ODbL attribution to render on
 * EVERY map, including the empty DU-00 skeleton. It stays put once real OSM/Overture
 * tile sources arrive (they add their own source attributions alongside it).
 *
 * This is the one attribution string the client owns, because it is a standing
 * legal obligation of the basemap — not a claim about any returned value.
 */
export const ODBL_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors, ODbL'

/** The exact plain-text credit the contract puts in `attribution[]` for OSM data. */
export const OSM_ATTRIBUTION = '© OpenStreetMap contributors'

/**
 * Does this server-sent string credit OpenStreetMap (i.e. is it already covered
 * by {@link ODBL_ATTRIBUTION})? Used only to avoid crediting OSM twice — never
 * to add a credit the server did not send.
 */
export function isOdblAttribution(attribution: string): boolean {
  return /openstreetmap/i.test(attribution)
}

function escapeHtml(text: string): string {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

/**
 * MapLibre control rendering the ODbL base credit plus whatever the last
 * `/sites` response required.
 *
 * Use with `attributionControl: false` on the map so there is exactly one
 * attribution line; {@link ./map!createMap} keeps the DU-00 built-in control as
 * its default for callers that render no data.
 */
export class OdblAttributionControl implements IControl {
  private readonly base: string
  private extra: string[] = []
  private odblRequiredByData = false
  private container: HTMLElement | null = null

  constructor(base: string = ODBL_ATTRIBUTION) {
    this.base = base
  }

  onAdd(_map: MapLibreMap): HTMLElement {
    const container = document.createElement('div')
    container.className =
      'maplibregl-ctrl maplibregl-ctrl-attrib siyur-attrib maplibregl-compact-show'
    this.container = container
    this.render()
    return container
  }

  onRemove(): void {
    this.container?.remove()
    this.container = null
  }

  getDefaultPosition(): 'bottom-right' {
    return 'bottom-right'
  }

  /** The element currently mounted on the map (`null` before `onAdd`). */
  get element(): HTMLElement | null {
    return this.container
  }

  /**
   * Drive the control from a response's `attribution[]`. OSM credits collapse
   * onto the always-present ODbL notice; every other string is rendered verbatim.
   */
  setResponseAttributions(attributions: readonly string[]): void {
    const seen = new Set<string>()
    const extra: string[] = []
    let odbl = false

    for (const raw of attributions) {
      const value = raw.trim()
      if (!value) continue
      if (isOdblAttribution(value)) {
        odbl = true
        continue
      }
      if (seen.has(value)) continue
      seen.add(value)
      extra.push(value)
    }

    this.extra = extra
    this.odblRequiredByData = odbl
    this.render()
  }

  /** Convenience wrapper: `control.setFromResponse(await fetchSites(...))`. */
  setFromResponse(response: Pick<SitesResponse, 'attribution'>): void {
    this.setResponseAttributions(response.attribution)
  }

  /** Reset to the DU-00 baseline (ODbL only) — e.g. when a fetch fails. */
  clearResponseAttributions(): void {
    this.setResponseAttributions([])
  }

  /**
   * The strings currently rendered, base first. OSM appears exactly once even
   * when the response also asks for it.
   */
  attributions(): string[] {
    return [this.base, ...this.extra]
  }

  /** Did the last response actually require an OSM/ODbL credit? */
  get isOdblRequiredByData(): boolean {
    return this.odblRequiredByData
  }

  private render(): void {
    const container = this.container
    if (!container) return
    container.dataset.odblRequired = String(this.odblRequiredByData)
    // Only the locally-owned base string may carry markup; server-sent strings
    // are escaped — attribution text is data, not a template.
    const html = [this.base, ...this.extra.map(escapeHtml)].join(' | ')
    container.innerHTML = `<div class="maplibregl-ctrl-attrib-inner">${html}</div>`
  }
}
