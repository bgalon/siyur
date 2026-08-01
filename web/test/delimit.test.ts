/**
 * The area-delimit control — US1's missing first step.
 *
 * The load-bearing assertions here are the genericity ones: the request the
 * control emits is built entirely from the map's bounds and the user's input, so
 * there is no place literal for the T063 AST scan to find (FR-001 / SC-005).
 */

import { describe, it, expect, vi } from 'vitest'

import {
  DelimitControl,
  isUsableBbox,
  mountDelimitControl,
  viewportBbox,
  type Bbox,
} from '../src/map/delimit'
import type { AreaRequest } from '../src/map/areas'

/** A `LngLatBounds`-shaped stand-in — the structural slice the control reads. */
const bounds = (west: number, south: number, east: number, north: number) => ({
  getWest: () => west,
  getSouth: () => south,
  getEast: () => east,
  getNorth: () => north,
})

interface Harness {
  readonly control: DelimitControl
  readonly root: HTMLElement
  readonly input: HTMLInputElement
  readonly form: HTMLFormElement
  readonly viewportButton: HTMLButtonElement
  readonly onDelimit: ReturnType<typeof vi.fn>
  readonly onError: ReturnType<typeof vi.fn>
}

const mount = (
  getBounds: () => ReturnType<typeof bounds>,
  onDelimit: (area: AreaRequest) => void | Promise<void> = vi.fn(),
): Harness => {
  const container = document.createElement('div')
  const spy = vi.fn(onDelimit)
  const onError = vi.fn()
  const control = mountDelimitControl(container, { getBounds, onDelimit: spy, onError })
  const root = control.element
  return {
    control,
    root,
    input: root.querySelector<HTMLInputElement>('.siyur-delimit__input')!,
    form: root.querySelector<HTMLFormElement>('.siyur-delimit__search')!,
    viewportButton: root.querySelector<HTMLButtonElement>('[data-action="use-viewport"]')!,
    onDelimit: spy,
    onError,
  }
}

/* ------------------------------------------------------------------ bbox --- */

describe('viewportBbox', () => {
  // Deliberately arbitrary numbers: the control has no opinion about *where*.
  it('reads [minLon, minLat, maxLon, maxLat] straight off the map bounds', () => {
    expect(viewportBbox(bounds(-3.125, 51.25, -3.1, 51.275))).toEqual([
      -3.125, 51.25, -3.1, 51.275,
    ])
  })

  it('clamps a world-wrapped viewport into valid EPSG:4326 ranges', () => {
    expect(viewportBbox(bounds(-190.5, -95, 205, 97))).toEqual([-180, -90, 180, 90])
  })

  it('rejects a degenerate extent instead of posting a 422', () => {
    expect(isUsableBbox([1, 2, 1, 3] as Bbox)).toBe(false)
    expect(isUsableBbox([1, 2, 3, 2] as Bbox)).toBe(false)
    expect(isUsableBbox([Number.NaN, 2, 3, 4] as Bbox)).toBe(false)
    expect(isUsableBbox([1, 2, 3, 4] as Bbox)).toBe(true)
  })
})

/* --------------------------------------------------------------- control --- */

describe('DelimitControl', () => {
  it('delimits by the CURRENT map view — bounds are read at click time', async () => {
    let view = bounds(1, 2, 3, 4)
    const harness = mount(() => view)

    harness.viewportButton.click()
    await Promise.resolve()
    expect(harness.onDelimit).toHaveBeenCalledWith({ bbox: [1, 2, 3, 4] })

    // Pan the map: the next delimit must follow it, not the mount-time extent.
    view = bounds(10, 20, 11, 21)
    harness.viewportButton.click()
    await Promise.resolve()
    expect(harness.onDelimit).toHaveBeenLastCalledWith({ bbox: [10, 20, 11, 21] })
  })

  it('delimits by the user-typed name — the client supplies no default', () => {
    const harness = mount(() => bounds(1, 2, 3, 4))
    harness.input.value = '  a place the user typed  '
    harness.form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))

    expect(harness.onDelimit).toHaveBeenCalledExactlyOnceWith({
      name: 'a place the user typed',
    })
  })

  it('does nothing on an empty search — an empty box is not an area', () => {
    const harness = mount(() => bounds(1, 2, 3, 4))
    harness.input.value = '   '
    harness.form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))
    expect(harness.onDelimit).not.toHaveBeenCalled()
  })

  it('refuses a zero-area viewport and says why, rather than posting it', () => {
    const harness = mount(() => bounds(5, 5, 5, 5))
    harness.viewportButton.click()

    expect(harness.onDelimit).not.toHaveBeenCalled()
    const hint = harness.root.querySelector<HTMLElement>('.siyur-delimit__hint')!
    expect(hint.hidden).toBe(false)
    expect(hint.textContent).toMatch(/no extent/i)
  })

  it('ships no place name, coordinate or default extent in its own markup (SC-005)', () => {
    const harness = mount(() => bounds(1, 2, 3, 4))
    // Every rendered string is UI chrome; nothing names a place, and the input
    // starts empty so no request can be sent without a user gesture.
    expect(harness.input.value).toBe('')
    expect(harness.root.textContent).not.toMatch(/\d+\.\d+/)
    expect(harness.onDelimit).not.toHaveBeenCalled()
  })

  it('blocks a double-submit while the resolve is in flight', async () => {
    let release = (): void => {}
    const pending = new Promise<void>((resolve) => {
      release = resolve
    })
    const harness = mount(() => bounds(1, 2, 3, 4), () => pending)

    harness.viewportButton.click()
    expect(harness.root.dataset.busy).toBe('true')
    expect(harness.viewportButton.disabled).toBe(true)

    harness.viewportButton.click() // ignored while busy
    release()
    await pending
    await Promise.resolve()

    expect(harness.onDelimit).toHaveBeenCalledOnce()
    expect(harness.root.dataset.busy).toBe('false')
    expect(harness.viewportButton.disabled).toBe(false)
  })

  it('reports a failing resolve instead of swallowing it', async () => {
    const harness = mount(() => bounds(1, 2, 3, 4), () => {
      throw new Error('401')
    })

    harness.viewportButton.click()
    await Promise.resolve()
    await Promise.resolve()

    expect(harness.onError).toHaveBeenCalledOnce()
    expect(harness.root.dataset.busy).toBe('false')
  })

  it('unmounts cleanly', () => {
    const harness = mount(() => bounds(1, 2, 3, 4))
    harness.control.destroy()
    expect(harness.root.isConnected).toBe(false)
  })
})
