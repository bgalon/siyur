/**
 * Phase A — the 20 disambiguation candidates a `404` already carries
 * (`docs/design/usable-m1-plan.md` § Phase A, journey 1).
 *
 * The defect this replaces: `POST /areas { name }` takes ~60 s and answers `404` with a
 * body listing every place the name could have meant; the client kept the status, dropped
 * the body, and told the user "not found" while the answer sat in the response.
 *
 * The load-bearing assertions, each named with the bug it catches:
 *
 * - **the candidates survive the error.** A `404` whose body lists them raises an error
 *   that *carries* them; a `404` with no candidates stays an ordinary failure;
 * - **nothing is chosen for the user, ever.** Rendering the list picks nothing — not even
 *   the highest-confidence option — and the list is not re-ordered by confidence. The
 *   server sent twenty *because it could not decide*, and deciding for it is how someone
 *   researches the wrong city for forty seconds before noticing;
 * - **a name is a commons-derived value and renders through the chip funnel or not at
 *   all** (ADR-0019 / FR-008): an unstamped candidate shows no name — and gets no pick
 *   button, because a button that identifies nothing is the wrong-city risk in a
 *   different shape;
 * - **nothing is dropped silently**: an option that cannot be chosen is shown as
 *   unchoosable, with the reason. Shortening twenty to eleven is the same class of bug as
 *   discarding all twenty.
 */

import { describe, expect, it, vi } from 'vitest'

// The `404` this endpoint really sends, generated from the app itself by
// `tests/test_api_areas.py::test_the_wire_capture_the_web_suite_doubles_is_the_body_this_endpoint_sends`
// and asserted equal to a live response there. Every double below is shaped from it, so this
// suite can no longer agree with itself about a body the server never sends (FAIL-017).
import wireCapture from './fixtures/area-404-wire.json'

import {
  AreaNotResolvedError,
  AreaRequestError,
  buildDisambiguation,
  mountDisambiguation,
  resolveArea,
  sanitiseAreaCandidates,
  type AreaCandidate,
} from '../src/map'

/* -------------------------------------------------------------- fixtures --- */

const OVERTURE = { kind: 'overture', license: 'CDLA-Permissive-2.0' }

/** One candidate exactly as `api/areas.py::_candidate` composes it. */
const wireCandidate = (name: string, confidence: number): Record<string, unknown> => ({
  name,
  confidence,
  bbox: [28.21, 36.41, 28.24, 36.47],
  source: OVERTURE,
})

const candidate = (over: Partial<AreaCandidate> = {}): AreaCandidate => ({
  name: 'Old Town',
  confidence: 0.8,
  bbox: [28.21, 36.41, 28.24, 36.47],
  source: OVERTURE,
  ...over,
})

/**
 * The `404` body **the server actually sends** — nested under `detail`.
 *
 * This fixture used to send `{message, candidates}` at the root, which is what
 * `areas.ts` used to read, so the suite was green while the feature had never once worked
 * in production (FAIL-017). Both halves of the test shared one wrong assumption and
 * neither was ever compared to a real response.
 *
 * FastAPI serialises `HTTPException(detail=X)` as `{"detail": X}`. If this shape is ever
 * "simplified" back to the root, `test_the_404_body_is_nested_under_detail` in
 * `tests/test_api_areas.py` is the thing that will still disagree.
 */
const notResolved = (candidates: readonly unknown[]): typeof fetch =>
  vi.fn(async () =>
    new Response(
      JSON.stringify({ detail: { message: 'several places match that name', candidates } }),
      { status: 404, headers: { 'Content-Type': 'application/json' } },
    ),
  ) as unknown as typeof fetch

/** The captured body, replayed verbatim — no hand-written shape between it and the client. */
const capturedNotResolved = (): typeof fetch =>
  vi.fn(async () =>
    new Response(JSON.stringify(wireCapture), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch

/* ---------------------------------------------------------------- client --- */

describe('the 404 body is the answer, not noise', () => {
  it('raises an error carrying all twenty candidates, in the server’s order', async () => {
    // Confidences deliberately **scrambled**, not descending: a client-side re-rank is
    // invisible against an already-sorted fixture, and re-ranking is precisely what this
    // asserts does not happen. The server sent twenty because it could not choose.
    const twenty = Array.from({ length: 20 }, (_, index) =>
      wireCandidate(`Match ${index}`, ((index * 7) % 20) / 20),
    )
    const error = await resolveArea({
      area: { name: 'ambiguous' },
      fetchImpl: notResolved(twenty),
    }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(AreaNotResolvedError)
    const resolved = error as AreaNotResolvedError
    expect(resolved.status).toBe(404)
    expect(resolved.detail).toBe('several places match that name')
    expect(resolved.candidates).toHaveLength(20)
    expect(resolved.candidates.map((c) => c.name)).toEqual(twenty.map((c) => c.name))
  })

  it('parses the captured body of a real 404, byte for byte', async () => {
    // The regression test for FAIL-017. It replays the generated capture rather than a
    // hand-written body, so it fails if `_unresolved_detail` moves, if FastAPI stops nesting
    // under `detail`, or if this client goes back to reading the root.
    const error = await resolveArea({
      area: { name: 'Old Town' },
      fetchImpl: capturedNotResolved(),
    }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(AreaNotResolvedError)
    const resolved = error as AreaNotResolvedError
    expect(resolved.candidates).toHaveLength(wireCapture.detail.candidates.length)
    expect(resolved.candidates.map((c) => c.name)).toEqual(
      wireCapture.detail.candidates.map((c) => c.name),
    )
    expect(resolved.detail).toBe(wireCapture.detail.message)
  })

  it('does not honour candidates at the root of the body', async () => {
    // The shape the old fixture invented. Accepting it too would keep this suite green
    // against a server that never sends it — which is the trap, not the fix.
    const rootShaped = vi.fn(async () =>
      new Response(JSON.stringify({ message: 'several match', candidates: [wireCandidate('X', 1)] }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    ) as unknown as typeof fetch

    const error = await resolveArea({ area: { name: 'x' }, fetchImpl: rootShaped }).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(AreaRequestError)
    expect(error).not.toBeInstanceOf(AreaNotResolvedError)
  })

  it('leaves a 404 with no candidates an ordinary failure', async () => {
    const error = await resolveArea({
      area: { name: 'nowhere' },
      fetchImpl: notResolved([]),
    }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(AreaRequestError)
    expect(error).not.toBeInstanceOf(AreaNotResolvedError)
  })

  it('narrows each candidate without repairing it', () => {
    const rows = sanitiseAreaCandidates([
      wireCandidate('Good', 0.9),
      { ...wireCandidate('Degenerate', 0.5), bbox: [28.21, 36.41, 28.21, 36.47] },
      { ...wireCandidate('Unstamped', 0.4), source: { kind: 'overture' } },
      { ...wireCandidate('Scoreless', 0.3), confidence: 'high' },
    ])
    expect(rows).toHaveLength(4) // nothing dropped
    expect(rows[1]?.bbox).toBeNull() // zero-width extent, not clamped
    expect(rows[2]?.source).toBeNull() // a half-stamp is not a stamp
    expect(rows[3]?.confidence).toBeNull()
  })
})

/* -------------------------------------------------------------- the list --- */

describe('the picker shows the options and chooses none of them', () => {
  it('renders one row per candidate, in the server’s order, never re-ranked', () => {
    const candidates = [
      candidate({ name: 'Third', confidence: 0.2 }),
      candidate({ name: 'First', confidence: 0.99 }),
      candidate({ name: 'Second', confidence: 0.5 }),
    ]
    const panel = buildDisambiguation(candidates, null)
    expect(panel.dataset.candidates).toBe('3')
    const names = [...panel.querySelectorAll('.siyur-value__text')].map((n) => n.textContent)
    // Confidence descends nowhere in this list, and that is the point: the order on
    // screen is the order on the wire.
    expect(names).toEqual(['Third', 'First', 'Second'])
    expect(
      [...panel.querySelectorAll<HTMLElement>('.siyur-disambiguation__item')].map(
        (item) => item.dataset.rank,
      ),
    ).toEqual(['0', '1', '2'])
  })

  it('picks nothing on mount, not even the highest-confidence option', () => {
    const onPick = vi.fn()
    const candidates = Array.from({ length: 20 }, (_, index) =>
      candidate({ name: `Match ${index}`, confidence: index === 7 ? 1 : 0.3 }),
    )
    buildDisambiguation(candidates, 'several places match that name', { onPick })
    expect(onPick).not.toHaveBeenCalled()
  })

  it('hands the chosen candidate’s own bbox back, verbatim', () => {
    const onPick = vi.fn()
    const wanted = candidate({ name: 'The other one', bbox: [10, 20, 11, 21] })
    const panel = buildDisambiguation([candidate(), wanted], null, { onPick })
    panel.querySelectorAll<HTMLButtonElement>('.siyur-disambiguation__pick')[1]?.click()
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick.mock.calls[0]?.[0]).toBe(wanted)
    expect((onPick.mock.calls[0]?.[0] as AreaCandidate).bbox).toEqual([10, 20, 11, 21])
  })

  it('states the server’s own message verbatim', () => {
    const panel = buildDisambiguation([candidate()], 'several places match that name')
    expect(panel.querySelector('.siyur-disambiguation__detail')?.textContent).toBe(
      'several places match that name',
    )
  })
})

describe('ADR-0019 — a candidate name reaches the DOM through the chip funnel or not at all', () => {
  it('renders the name with its source stamp beside it', () => {
    const panel = buildDisambiguation([candidate()], null)
    const chip = panel.querySelector<HTMLElement>('.siyur-chip')
    expect(chip?.dataset.sourceKind).toBe('overture')
    expect(chip?.dataset.license).toBe('CDLA-Permissive-2.0')
    expect(panel.querySelector('.siyur-value__text')?.textContent).toBe('Old Town')
  })

  it('shows no name, and no way to choose it, when the stamp is missing', () => {
    const onPick = vi.fn()
    const panel = buildDisambiguation([candidate({ source: null })], null, { onPick })
    const item = panel.querySelector<HTMLElement>('.siyur-disambiguation__item')
    expect(item?.dataset.candidate).toBe('unstamped')
    expect(panel.textContent).not.toContain('Old Town')
    // No control at all: an option nobody can identify is one nobody may choose.
    expect(panel.querySelector('.siyur-disambiguation__pick')).toBeNull()
    expect(item?.querySelector('.siyur-disambiguation__note')?.textContent).toContain(
      'no source stamp',
    )
  })

  it('withholds a name behind a HALF-stamp, because the funnel is what decides', () => {
    // A source object that exists but names no licence passes any `!== null` check and
    // still fails `isSourceRef` — so this proves the chip funnel is doing the work, not a
    // null-check that happens to sit in front of it. `{ kind }` with no `license` is the
    // half-stamp `guards.ts` refuses: the client would have to invent the missing half.
    const onPick = vi.fn()
    const panel = buildDisambiguation(
      [candidate({ source: { kind: 'overture' } as never })],
      null,
      { onPick },
    )
    expect(panel.textContent).not.toContain('Old Town')
    expect(panel.querySelector('.siyur-chip')).toBeNull()
    expect(panel.querySelector('.siyur-disambiguation__pick')).toBeNull()
    expect(
      panel.querySelector<HTMLElement>('.siyur-disambiguation__item')?.dataset.candidate,
    ).toBe('unstamped')
  })

  it('shows a stamped name with no usable extent, and says why it cannot be opened', () => {
    const panel = buildDisambiguation([candidate({ bbox: null })], null, { onPick: vi.fn() })
    const item = panel.querySelector<HTMLElement>('.siyur-disambiguation__item')
    expect(item?.dataset.candidate).toBe('unusable')
    expect(item?.querySelector('.siyur-value__text')?.textContent).toBe('Old Town')
    expect(panel.querySelector('.siyur-disambiguation__pick')).toBeNull()
    expect(item?.querySelector('.siyur-disambiguation__note')?.textContent).toContain(
      'no usable extent',
    )
  })

  it('captions the match scores as computed, not sourced', () => {
    const panel = buildDisambiguation([candidate()], null)
    const caption = panel.querySelector<HTMLElement>('.siyur-disambiguation__caption')
    expect(caption?.dataset.scoreSource).toBe('server-computed')
    expect(caption?.textContent).toContain('not sourced data')
    expect(panel.querySelector('.siyur-disambiguation__confidence')?.textContent).toBe(
      'MATCH 0.8',
    )
  })

  it('puts a hostile candidate name on the page as text, never as markup', () => {
    const panel = buildDisambiguation(
      [candidate({ name: '<img src=x onerror="alert(1)">' })],
      null,
    )
    expect(panel.querySelector('.siyur-value__text')?.textContent).toContain('<img')
    expect(panel.querySelector('img')).toBeNull()
  })
})

/* ------------------------------------------------------------- surface --- */

describe('the mounted picker never leaves a stale list on screen', () => {
  it('replaces the previous search’s candidates and clears on a resolve', () => {
    const host = document.createElement('div')
    const surface = mountDisambiguation(host)

    surface.show(new AreaNotResolvedError('first', [candidate({ name: 'First' })]))
    expect(host.querySelectorAll('.siyur-disambiguation')).toHaveLength(1)
    expect(host.textContent).toContain('First')

    surface.show(new AreaNotResolvedError('second', [candidate({ name: 'Second' })]))
    // One panel, not two: a stale list is an invitation to delimit the wrong place.
    expect(host.querySelectorAll('.siyur-disambiguation')).toHaveLength(1)
    expect(host.textContent).not.toContain('First')

    surface.clear()
    expect(host.querySelector('.siyur-disambiguation')).toBeNull()
    expect(surface.element).toBeNull()
  })

  it('offers a way out that chooses nothing', () => {
    const onDismiss = vi.fn()
    const onPick = vi.fn()
    const host = document.createElement('div')
    const surface = mountDisambiguation(host, { onPick, onDismiss })
    surface.show(new AreaNotResolvedError(null, [candidate()]))
    host.querySelector<HTMLButtonElement>('.siyur-disambiguation__dismiss')?.click()
    expect(onDismiss).toHaveBeenCalledTimes(1)
    expect(onPick).not.toHaveBeenCalled()
  })
})
