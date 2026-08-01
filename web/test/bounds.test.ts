import { describe, expect, it } from 'vitest'

import { boundsOfPolygon } from '../src/map/bounds'
import type { GeoPolygon } from '../src/map/types'

const polygon = (ring: readonly (readonly [number, number])[]): GeoPolygon => ({
  type: 'Polygon',
  coordinates: [ring],
})

describe('boundsOfPolygon', () => {
  it('returns the extent of a ring, lon first', () => {
    // A real resolved area (the Rhodes old-town bbox), lon/lat as EPSG:4326.
    const ring = [
      [28.229, 36.4405],
      [28.229, 36.4465],
      [28.222, 36.4465],
      [28.222, 36.4405],
      [28.229, 36.4405],
    ] as const
    expect(boundsOfPolygon(polygon(ring))).toEqual([
      [28.222, 36.4405],
      [28.229, 36.4465],
    ])
  })

  it('refuses a transposed ring instead of framing the wrong place', () => {
    // The trap: 36.44 is a legal longitude and 28.22 a legal latitude, so a
    // swapped pair passes every range check. Only per-axis validation catches
    // it — and here the swap pushes latitude past 90, which must be refused.
    const swapped = [
      [36.4405, 28.229],
      [36.4465, 28.229],
      [128.4465, 28.222],
      [36.4405, 128.222],
      [36.4405, 28.229],
    ] as const
    expect(boundsOfPolygon(polygon(swapped))).toBeNull()
  })

  it.each([
    ['longitude out of range', [[181, 10]]],
    ['latitude out of range', [[10, 91]]],
    ['a non-numeric vertex', [[10, Number.NaN]]],
  ])('refuses %s', (_label, ring) => {
    expect(boundsOfPolygon(polygon(ring as never))).toBeNull()
  })

  it('refuses an empty or absent polygon rather than throwing', () => {
    expect(boundsOfPolygon(null)).toBeNull()
    expect(boundsOfPolygon(undefined)).toBeNull()
    expect(boundsOfPolygon(polygon([]))).toBeNull()
  })

  it('does not skip a bad vertex and frame a partial area', () => {
    // Silently ignoring the invalid vertex would yield a plausible-but-wrong
    // extent — the failure mode this returns null to avoid.
    const ring = [
      [28.222, 36.4405],
      [28.229, 36.4465],
      [999, 36.44],
    ] as const
    expect(boundsOfPolygon(polygon(ring))).toBeNull()
  })
})
