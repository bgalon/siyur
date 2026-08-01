import { describe, it, expect } from 'vitest'
import type { Map as MapLibreMap } from 'maplibre-gl'

import {
  chipTextFromSource,
  createAttributionChip,
  renderSourcedValue,
} from '../src/map/attribution-chip'
import {
  ODBL_ATTRIBUTION,
  OSM_ATTRIBUTION,
  OdblAttributionControl,
  isOdblAttribution,
} from '../src/map/attribution'
import type { SourceRef, SourcedValue } from '../src/map/types'

// Stamps lifted verbatim from the contract's worked example
// (specs/001-research-cited-sites/contracts/sites.md).
const OVERTURE: SourceRef = {
  kind: 'overture',
  id: '08f394…',
  license: 'CDLA-Permissive-2.0',
  attribution: null,
}
const OSM: SourceRef = {
  kind: 'osm',
  id: 'node/123456',
  license: 'ODbL-1.0',
  attribution: '© OpenStreetMap contributors',
}

const stamped = <T>(value: T, source: SourceRef, bundleable = true): SourcedValue<T> => ({
  value,
  source,
  bundleable,
  confidence: 0.8,
  observed_at: '2026-07-22',
})

describe('chip text comes ONLY from the value’s own source stamp (FR-004)', () => {
  it('renders kind + license from the stamp', () => {
    expect(chipTextFromSource(OVERTURE)).toBe('OVERTURE · CDLA-Permissive-2.0')
  })

  it('appends the stamped attribution string verbatim when the stamp carries one', () => {
    expect(chipTextFromSource(OSM)).toBe('OSM · ODbL-1.0 · © OpenStreetMap contributors')
  })

  it('never infers attribution the stamp does not carry', () => {
    // Same OSM source, but the stamp omits `attribution`. The client must NOT
    // fill in "© OpenStreetMap contributors" from its knowledge of ODbL.
    const bare: SourceRef = { kind: 'osm', id: 'node/1', license: 'ODbL-1.0' }
    expect(chipTextFromSource(bare)).toBe('OSM · ODbL-1.0')
    expect(chipTextFromSource(bare)).not.toMatch(/contributors/)
  })

  it('has no lookup table — an unknown kind/license passes straight through', () => {
    const exotic: SourceRef = { kind: 'ministry-of-maps', id: 'x', license: 'XYZ-1.0' }
    expect(chipTextFromSource(exotic)).toBe('MINISTRY-OF-MAPS · XYZ-1.0')
  })

  it('exposes kind, license and bundleability as data attributes', () => {
    const chip = createAttributionChip(stamped('Ρολόι', OSM))
    expect(chip?.dataset.sourceKind).toBe('osm')
    expect(chip?.dataset.license).toBe('ODbL-1.0')
    expect(chip?.dataset.bundleable).toBe('true')

    const notBundleable = createAttributionChip(
      stamped('4.6★', { kind: 'review_provider', id: 'p', license: 'proprietary' }, false),
    )
    // Dashed encoding: this value may never enter an offline bundle.
    expect(notBundleable?.dataset.bundleable).toBe('false')
  })
})

describe('a value without a source is NEVER rendered (FR-003 / SC-002)', () => {
  const unrenderable: [string, unknown][] = [
    ['null', null],
    ['undefined', undefined],
    ['a bare fact (no wrapper)', 'Palace of the Grand Master'],
    ['a stamp-less wrapper', { value: 'Palace of the Grand Master' }],
    ['an explicitly null source', { value: 'Palace', source: null }],
    ['a source with no license', { value: 'Palace', source: { kind: 'osm', id: 'node/1' } }],
    ['a source with no kind', { value: 'Palace', source: { license: 'ODbL-1.0' } }],
    ['a source with blank strings', { value: 'Palace', source: { kind: ' ', license: ' ' } }],
  ]

  it.each(unrenderable)('createAttributionChip returns null for %s', (_label, value) => {
    expect(createAttributionChip(value)).toBeNull()
  })

  it.each(unrenderable)('renderSourcedValue emits no DOM at all for %s', (_label, value) => {
    // Not "renders without a chip" — renders NOTHING. There is no code path from
    // an unstamped value to the document.
    expect(renderSourcedValue(value as SourcedValue<string> | null)).toBeNull()
  })

  it('renders the value together with its chip once it IS stamped', () => {
    const element = renderSourcedValue(stamped('Palace of the Grand Master', OVERTURE), {
      label: 'EN',
    })
    expect(element).not.toBeNull()
    expect(element?.querySelector('.siyur-value__text')?.textContent).toBe(
      'Palace of the Grand Master',
    )
    expect(element?.querySelector('.siyur-chip')?.textContent).toBe(
      'OVERTURE · CDLA-Permissive-2.0',
    )
    expect(element?.querySelector('.siyur-value__label')?.textContent).toBe('EN')
  })

  it('never renders a value element without a chip inside it', () => {
    const element = renderSourcedValue(stamped('Ρολόι', OSM))
    expect(element?.querySelectorAll('.siyur-chip')).toHaveLength(1)
  })
})

describe('ODbL attribution control (T044, FR-004 / Constitution Article V)', () => {
  const mount = (): { control: OdblAttributionControl; element: HTMLElement } => {
    const control = new OdblAttributionControl()
    const element = control.onAdd({} as unknown as MapLibreMap)
    return { control, element }
  }

  it('renders the ODbL notice unconditionally, before any response (DU-00)', () => {
    const { element } = mount()
    expect(element.textContent).toMatch(/OpenStreetMap/)
    expect(element.textContent).toMatch(/ODbL/)
    expect(element.dataset.odblRequired).toBe('false')
  })

  it('flags OSM credit as data-required for an OSM-derived response', () => {
    const { control, element } = mount()
    control.setFromResponse({ attribution: [OSM_ATTRIBUTION] })
    expect(element.dataset.odblRequired).toBe('true')
    expect(element.textContent).toMatch(/OpenStreetMap contributors/)
  })

  it('credits OpenStreetMap exactly once even when the response repeats it', () => {
    const { control, element } = mount()
    control.setFromResponse({
      attribution: [OSM_ATTRIBUTION, '© OpenStreetMap contributors'],
    })
    expect(control.attributions()).toEqual([ODBL_ATTRIBUTION])
    expect(element.textContent?.match(/OpenStreetMap/g)).toHaveLength(1)
  })

  it('renders non-OSM credits from the response alongside the base notice', () => {
    const { control, element } = mount()
    control.setFromResponse({
      attribution: [OSM_ATTRIBUTION, 'Wikivoyage: Rhodes (CC BY-SA 4.0)'],
    })
    expect(control.attributions()).toEqual([
      ODBL_ATTRIBUTION,
      'Wikivoyage: Rhodes (CC BY-SA 4.0)',
    ])
    expect(element.textContent).toMatch(/Wikivoyage: Rhodes \(CC BY-SA 4\.0\)/)
  })

  it('adds nothing of its own when the response requires no extra credit', () => {
    const { control, element } = mount()
    control.setFromResponse({ attribution: [] })
    expect(control.attributions()).toEqual([ODBL_ATTRIBUTION])
    expect(control.isOdblRequiredByData).toBe(false)
    // The base ODbL line stays — it is the basemap obligation, not a data claim.
    expect(element.textContent).toMatch(/OpenStreetMap/)
  })

  it('drops a stale credit when a later response no longer requires it', () => {
    const { control, element } = mount()
    control.setResponseAttributions(['Wikivoyage: Rhodes (CC BY-SA 4.0)'])
    control.setResponseAttributions([OSM_ATTRIBUTION])
    expect(element.textContent).not.toMatch(/Wikivoyage/)
  })

  it('escapes server-sent attribution strings (only the base string carries markup)', () => {
    const { control, element } = mount()
    control.setResponseAttributions(['<img src=x onerror="boom()">'])
    expect(element.querySelector('img')).toBeNull()
    expect(element.textContent).toMatch(/<img src=x/)
  })

  it('isOdblAttribution recognises the contract string', () => {
    expect(isOdblAttribution(OSM_ATTRIBUTION)).toBe(true)
    expect(isOdblAttribution('Wikivoyage: Rhodes (CC BY-SA 4.0)')).toBe(false)
  })
})
