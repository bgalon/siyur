import { describe, it, expect, vi } from 'vitest'

// jsdom has no WebGL, so we stub MapLibre's Map to assert wiring (style + attribution +
// container) without a GL context. The empty-style and attribution constants below are
// exercised for real. The genuine render is covered by the airplane-mode e2e (DU-06).
vi.mock('maplibre-gl', () => {
  class FakeMap {
    constructor(public opts: Record<string, unknown>) {}
    getContainer() {
      return this.opts.container
    }
    remove() {}
  }
  return { default: { Map: FakeMap }, Map: FakeMap }
})

const { EMPTY_STYLE, ODBL_ATTRIBUTION, createMap } = await import('../src/map')

describe('empty MapLibre style', () => {
  it('is a version-8 style with no tile sources', () => {
    expect(EMPTY_STYLE.version).toBe(8)
    expect(Object.keys(EMPTY_STYLE.sources)).toHaveLength(0)
  })

  it('renders a single background layer (blank map, no network)', () => {
    expect(EMPTY_STYLE.layers).toHaveLength(1)
    expect(EMPTY_STYLE.layers[0]?.type).toBe('background')
  })
})

describe('ODbL attribution (Constitution Article V)', () => {
  it('names OpenStreetMap and ODbL', () => {
    expect(ODBL_ATTRIBUTION).toMatch(/OpenStreetMap/)
    expect(ODBL_ATTRIBUTION).toMatch(/ODbL/)
  })
})

describe('createMap', () => {
  it('mounts on the container with the empty style and forces ODbL attribution', () => {
    const container = document.createElement('div')
    const map = createMap(container) as unknown as {
      opts: { style: unknown; attributionControl: { customAttribution: string } }
      getContainer: () => HTMLElement
    }
    expect(map.getContainer()).toBe(container)
    expect(map.opts.style).toBe(EMPTY_STYLE)
    expect(map.opts.attributionControl.customAttribution).toBe(ODBL_ATTRIBUTION)
  })
})
