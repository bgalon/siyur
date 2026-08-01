/**
 * The research pass — SSE framing, provenance gating, and honest reporting.
 *
 * `POST /areas/{area_id}/research` does not exist yet; the CONTRACT is the
 * interface, so the frame sequence below is `contracts/research.md`'s own worked
 * example. The load-bearing assertions are:
 *
 * - a frame **split across two chunks** is parsed correctly (the case a naive
 *   `split('\n\n')` gets wrong, and the one that actually happens on the wire);
 * - a degraded pass is reported as **partial**, never as complete (FR-012);
 * - `summary.sites === 0` renders an explicit "nothing found", never a
 *   fabricated placeholder (SC-006);
 * - an unstamped `site` frame is dropped rather than rendered (FR-003).
 */

import { describe, it, expect, vi } from 'vitest'

import {
  ResearchProgressSurface,
  ResearchRequestError,
  SseFrameParser,
  describeStatus,
  runResearch,
  streamResearch,
} from '../src/map/research'
import { sanitiseResearchStatus, sanitiseResearchSummary } from '../src/map/guards'

/* -------------------------------------------------------------- fixtures --- */

const STAMPED_SITE = {
  id: 'site-1',
  names: {
    en: { value: 'A researched place', source: { kind: 'osm', license: 'ODbL-1.0' } },
  },
  location: {
    value: { type: 'Point', coordinates: [1.5, 2.5] },
    source: { kind: 'overture', license: 'CDLA-Permissive-2.0' },
  },
}

/** The contract's worked sequence, verbatim in shape. */
const CONTRACT_STREAM =
  'event: status\ndata: {"phase":"resolve_area","msg":"polygon ready"}\n\n' +
  'event: status\ndata: {"phase":"research","source":"overture","found":31}\n\n' +
  'event: status\ndata: {"phase":"research","source":"osm","found":18,"degraded":false}\n\n' +
  'event: status\ndata: {"phase":"curate","merged":39,"conflicts":4}\n\n' +
  `event: site\ndata: ${JSON.stringify(STAMPED_SITE)}\n\n` +
  'event: summary\ndata: {"sites":39,"new":39,"reused":0,"conflicts":4,' +
  '"sources":{"overture":31,"osm":18},"degraded_sources":[]}\n\n' +
  'event: done\ndata: {"area_id":"9c1e-uuid"}\n\n'

const REQUEST = { area_id: '9c1e-uuid', force_refresh: false } as const

/** A `fetch` answering `200 text/event-stream` with `chunks` in order. */
const streamingFetch = (chunks: readonly string[], status = 200): typeof fetch =>
  vi.fn(async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        controller.close()
      },
    })
    return new Response(status === 200 ? body : null, {
      status,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  }) as unknown as typeof fetch

/* ---------------------------------------------------------------- framer --- */

describe('SseFrameParser', () => {
  it('frames the contract sequence in one chunk', () => {
    const frames = new SseFrameParser().push(CONTRACT_STREAM)
    expect(frames.map((f) => f.event)).toEqual([
      'status',
      'status',
      'status',
      'status',
      'site',
      'summary',
      'done',
    ])
  })

  it('buffers a frame split MID-WAY across two chunks', () => {
    const parser = new SseFrameParser()

    // Chunk 1 stops inside the JSON payload — no frame is complete yet.
    expect(parser.push('event: status\ndata: {"phase":"resea')).toEqual([])
    expect(parser.pending).not.toBe('')

    // Chunk 2 completes it and carries the next frame whole.
    const frames = parser.push('rch","source":"osm","found":18}\n\nevent: done\ndata: {}\n\n')
    expect(frames).toHaveLength(2)
    expect(frames[0]).toEqual({
      event: 'status',
      data: '{"phase":"research","source":"osm","found":18}',
    })
    expect(frames[1]?.event).toBe('done')
    expect(parser.pending).toBe('')
  })

  it('splits at every possible byte boundary of the contract stream', () => {
    // Exhaustive: for every split point, the same seven frames must come out.
    const expected = new SseFrameParser().push(CONTRACT_STREAM)
    for (let cut = 1; cut < CONTRACT_STREAM.length; cut += 1) {
      const parser = new SseFrameParser()
      const frames = [
        ...parser.push(CONTRACT_STREAM.slice(0, cut)),
        ...parser.push(CONTRACT_STREAM.slice(cut)),
      ]
      expect(frames, `split at ${cut}`).toEqual(expected)
    }
  })

  it('does not mistake a \\r\\n straddling a chunk boundary for a frame end', () => {
    const parser = new SseFrameParser()
    expect(parser.push('event: status\r\ndata: {"phase":"research"}\r')).toEqual([])
    expect(parser.push('\n\r\n')).toEqual([
      { event: 'status', data: '{"phase":"research"}' },
    ])
  })

  it('ignores keep-alive comments and joins multi-line data', () => {
    const frames = new SseFrameParser().push(': keep-alive\n\nevent: x\ndata: a\ndata: b\n\n')
    expect(frames).toEqual([{ event: 'x', data: 'a\nb' }])
  })

  it('flushes an unterminated tail when the server closes early', () => {
    const parser = new SseFrameParser()
    expect(parser.push('event: done\ndata: {"area_id":"a"}')).toEqual([])
    expect(parser.flush()).toEqual([{ event: 'done', data: '{"area_id":"a"}' }])
    expect(parser.flush()).toEqual([])
  })
})

/* ---------------------------------------------------------------- guards --- */

describe('research frame guards', () => {
  it('drops a status frame with no phase rather than labelling it "working"', () => {
    expect(sanitiseResearchStatus({ source: 'osm', found: 3 })).toBeNull()
  })

  it('reads only the fields the frame carried', () => {
    expect(sanitiseResearchStatus({ phase: 'research', source: 'osm', found: 18 })).toEqual({
      phase: 'research',
      msg: null,
      source: 'osm',
      found: 18,
      degraded: false,
    })
  })

  it('flags an unreadable summary instead of reporting zeroed totals as fact', () => {
    const summary = sanitiseResearchSummary('not a frame')
    expect(summary.readable).toBe(false)
    expect(summary.sites).toBe(0)
    expect(sanitiseResearchSummary({ sites: 0 }).readable).toBe(true)
  })

  it('keeps degraded_sources verbatim (FR-012)', () => {
    expect(
      sanitiseResearchSummary({ sites: 4, degraded_sources: ['osm', '', 7] }).degraded_sources,
    ).toEqual(['osm'])
  })
})

describe('describeStatus', () => {
  it('names the phase, the source and the count from the frame alone', () => {
    expect(
      describeStatus({ phase: 'research', source: 'osm', found: 18, degraded: true, msg: null }),
    ).toBe('research · osm · 18 found · degraded')
    expect(
      describeStatus({ phase: 'resolve_area', source: null, found: null, degraded: false, msg: 'polygon ready' }),
    ).toBe('resolve_area · polygon ready')
  })
})

/* -------------------------------------------------------------- streaming --- */

describe('streamResearch', () => {
  it('POSTs force_refresh and asks for an event stream', async () => {
    const fetchImpl = streamingFetch([CONTRACT_STREAM])
    await streamResearch({
      request: { area_id: '9c1e-uuid', force_refresh: true },
      token: 'tok-123',
      fetchImpl,
    })

    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ]
    expect(url).toBe('/areas/9c1e-uuid/research')
    expect(init.method).toBe('POST')
    const headers = init.headers as Record<string, string>
    expect(headers.Accept).toBe('text/event-stream')
    expect(headers.Authorization).toBe('Bearer tok-123')
    expect(JSON.parse(init.body as string)).toEqual({ force_refresh: true })
  })

  it('dispatches the contract sequence — even when chunked mid-frame', async () => {
    const cut = CONTRACT_STREAM.indexOf('"sources"') + 4 // inside the summary frame
    const onStatus = vi.fn()
    const onSite = vi.fn()
    const onSummary = vi.fn()
    const onDone = vi.fn()

    const summary = await streamResearch({
      request: REQUEST,
      fetchImpl: streamingFetch([CONTRACT_STREAM.slice(0, cut), CONTRACT_STREAM.slice(cut)]),
      handlers: { onStatus, onSite, onSummary, onDone },
    })

    expect(onStatus).toHaveBeenCalledTimes(4)
    expect(onSite).toHaveBeenCalledOnce()
    expect(onDone).toHaveBeenCalledWith('9c1e-uuid')
    expect(onSummary).toHaveBeenCalledOnce()
    expect(summary?.sites).toBe(39)
    expect(summary?.sources).toEqual({ overture: 31, osm: 18 })
  })

  it('drops an UNSTAMPED site frame rather than rendering it (FR-003)', async () => {
    const onSite = vi.fn()
    await streamResearch({
      request: REQUEST,
      fetchImpl: streamingFetch([
        'event: site\ndata: {"id":"x","names":{},"location":{"value":{"type":"Point","coordinates":[1,2]}}}\n\n',
      ]),
      handlers: { onSite },
    })
    expect(onSite).not.toHaveBeenCalled()
  })

  it('survives a malformed frame without aborting the pass', async () => {
    const onDone = vi.fn()
    await streamResearch({
      request: REQUEST,
      fetchImpl: streamingFetch(['event: status\ndata: {oops\n\nevent: done\ndata: {}\n\n']),
      handlers: { onStatus: vi.fn(), onDone },
    })
    expect(onDone).toHaveBeenCalledWith(null)
  })

  it.each([401, 404, 409])('raises ResearchRequestError on %i', async (status) => {
    await expect(
      streamResearch({ request: REQUEST, fetchImpl: streamingFetch([], status) }),
    ).rejects.toBeInstanceOf(ResearchRequestError)
  })

  it('returns null when the stream ends with no summary', async () => {
    const summary = await streamResearch({
      request: REQUEST,
      fetchImpl: streamingFetch(['event: status\ndata: {"phase":"research"}\n\n']),
    })
    expect(summary).toBeNull()
  })
})

/* --------------------------------------------------------------- surface --- */

describe('ResearchProgressSurface', () => {
  const mount = () => {
    const container = document.createElement('div')
    const surface = new ResearchProgressSurface(container)
    return { container, surface, root: surface.element }
  }

  const run = async (chunks: readonly string[]) => {
    const harness = mount()
    await runResearch(harness.surface, { request: REQUEST, fetchImpl: streamingFetch(chunks) })
    return harness
  }

  it('starts idle and hidden — no pass runs on mount', () => {
    const { surface, root } = mount()
    expect(surface.currentState).toBe('idle')
    expect(root.hidden).toBe(true)
  })

  it('reports the phase, the per-source counts and the totals of a clean pass', async () => {
    const { surface, root } = await run([CONTRACT_STREAM])

    expect(surface.currentState).toBe('complete')
    expect(root.dataset.phase).toBe('curate')
    expect(root.querySelector('.siyur-research__count')?.textContent).toBe('1 cited place so far')

    const sources = [...root.querySelectorAll<HTMLElement>('.siyur-research__source')]
    expect(sources.map((li) => li.dataset.source)).toEqual(['overture', 'osm'])
    expect(sources.map((li) => li.dataset.found)).toEqual(['31', '18'])
    expect(root.querySelector('.siyur-research__result')?.textContent).toBe(
      '39 cited places · 39 new · 0 reused · 4 conflicts',
    )
  })

  it('shows a degraded pass as PARTIAL and names the failed source (FR-012)', async () => {
    const { surface, root } = await run([
      'event: status\ndata: {"phase":"research","source":"osm","found":0,"degraded":true}\n\n' +
        'event: summary\ndata: {"sites":12,"new":12,"reused":0,"conflicts":0,' +
        '"sources":{"overture":12,"osm":0},"degraded_sources":["osm"]}\n\n' +
        'event: done\ndata: {"area_id":"a"}\n\n',
    ])

    expect(surface.currentState).toBe('partial')
    expect(root.dataset.state).toBe('partial')
    const result = root.querySelector('.siyur-research__result')?.textContent ?? ''
    expect(result).toMatch(/^Partial result/)
    expect(result).toMatch(/Degraded: osm/)
    expect(
      root.querySelector<HTMLElement>('[data-source="osm"]')?.dataset.degraded,
    ).toBe('true')
  })

  it('says "nothing found here" for an empty area — no placeholder (SC-006)', async () => {
    const { surface, root } = await run([
      'event: summary\ndata: {"sites":0,"new":0,"reused":0,"conflicts":0,' +
        '"sources":{},"degraded_sources":[]}\n\nevent: done\ndata: {"area_id":"a"}\n\n',
    ])

    expect(surface.currentState).toBe('empty')
    expect(root.querySelector('.siyur-research__result')?.textContent).toBe('Nothing found here.')
    expect(root.querySelector('.siyur-research__count')?.textContent).toBe('')
  })

  it('never calls a truncated stream complete', async () => {
    const { surface, root } = await run([
      'event: status\ndata: {"phase":"research","source":"osm","found":3}\n\n',
    ])

    expect(surface.currentState).toBe('incomplete')
    expect(root.querySelector('.siyur-research__result')?.textContent).toMatch(/may be incomplete/)
  })

  it('never calls an unreadable summary complete either', async () => {
    const { surface } = await run(['event: summary\ndata: "nonsense"\n\n'])
    expect(surface.currentState).toBe('incomplete')
  })

  it('reports a failed pass and keeps the error state', async () => {
    const { surface, root } = mount()
    await runResearch(surface, { request: REQUEST, fetchImpl: streamingFetch([], 401) })

    expect(surface.currentState).toBe('error')
    expect(root.querySelector('.siyur-research__result')?.textContent).toMatch(/401/)

    surface.finish() // must not promote an error into a result
    expect(surface.currentState).toBe('error')
  })

  it('clears the previous pass before the next one', async () => {
    const harness = mount()
    await runResearch(harness.surface, {
      request: REQUEST,
      fetchImpl: streamingFetch([CONTRACT_STREAM]),
    })
    await runResearch(harness.surface, {
      request: REQUEST,
      fetchImpl: streamingFetch([
        'event: summary\ndata: {"sites":0,"sources":{},"degraded_sources":[]}\n\n' +
          'event: done\ndata: {}\n\n',
      ]),
    })

    expect(harness.surface.currentState).toBe('empty')
    expect(harness.root.querySelectorAll('.siyur-research__source')).toHaveLength(0)
    expect(harness.root.querySelector('.siyur-research__result')?.textContent).toBe(
      'Nothing found here.',
    )
  })

  it('forwards each stamped site so a caller can render incrementally', async () => {
    const onSite = vi.fn()
    const container = document.createElement('div')
    const surface = new ResearchProgressSurface(container, { onSite })
    await runResearch(surface, { request: REQUEST, fetchImpl: streamingFetch([CONTRACT_STREAM]) })

    expect(onSite).toHaveBeenCalledOnce()
    expect(onSite.mock.calls[0]?.[0]).toMatchObject({ id: 'site-1' })
  })
})
