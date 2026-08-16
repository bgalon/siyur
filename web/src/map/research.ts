/**
 * The research pass: `POST /areas/{area_id}/research` as a live SSE stream.
 *
 * Contract: `specs/001-research-cited-sites/contracts/research.md`. The endpoint
 * is being built by a sibling session; the contract is the interface, so this
 * module is written and tested against the contract's own worked frame sequence.
 *
 * Three things this module refuses to do:
 *
 * 1. **Invent progress.** Every word on screen is built from a frame's own
 *    fields — the phase name, the source name, the counts. A frame the guard
 *    could not read renders nothing at all rather than a reassuring placeholder.
 * 2. **Report a partial answer as complete.** `summary.degraded_sources`
 *    (FR-012) puts the surface into an explicit `partial` state; a stream that
 *    ends with no summary at all is `incomplete`, never `complete`.
 * 3. **Fabricate a result for an empty area.** `summary.sites === 0` renders an
 *    explicit "nothing found here" (SC-006 / FR-002).
 *
 * `EventSource` cannot issue a POST (and carries no `Authorization` header), so
 * the stream is read with `fetch` + a `ReadableStream` reader and framed by
 * {@link SseFrameParser} below.
 */

import { researchPath, type ResearchRequest } from './areas'
import { ElapsedTimer, type ElapsedOptions } from './elapsed'
import {
  sanitiseResearchDone,
  sanitiseResearchStatus,
  sanitiseResearchSummary,
  sanitiseSite,
} from './guards'
import type { ResearchStatus, ResearchSummary, SiteRecordV1 } from './types'

/* --------------------------------------------------------- SSE framing --- */

/** One parsed `event:`/`data:` frame. */
export interface SseFrame {
  /** The `event:` field, or SSE's `message` default. */
  readonly event: string
  /** The joined `data:` payload (multiple `data:` lines join with `\n`). */
  readonly data: string
}

/**
 * Parse one frame block (the text between blank lines) per the SSE grammar:
 * `field: value`, a leading space after the colon is stripped, `:`-prefixed
 * lines are comments. Returns `null` for a block carrying no `data:` at all —
 * a keep-alive comment is not an event.
 */
function parseFrameBlock(block: string): SseFrame | null {
  let event = 'message'
  const data: string[] = []

  for (const line of block.split('\n')) {
    if (line === '' || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }

  return data.length > 0 ? { event, data: data.join('\n') } : null
}

/**
 * Incremental SSE framer.
 *
 * **The case a naive `split('\n\n')` gets wrong** is the one that actually
 * happens on the wire: a frame arriving across two network chunks. The parser
 * therefore buffers, emits only whole frames, and keeps the remainder for the
 * next chunk. A trailing lone `\r` is held back too, so a `\r\n` straddling a
 * chunk boundary is not mistaken for a frame terminator.
 */
export class SseFrameParser {
  private buffer = ''

  /** Feed a decoded chunk; returns every frame that is now complete. */
  push(chunk: string): SseFrame[] {
    this.buffer += chunk
    this.normaliseLineEndings()

    const frames: SseFrame[] = []
    for (;;) {
      const end = this.buffer.indexOf('\n\n')
      if (end === -1) break
      const frame = parseFrameBlock(this.buffer.slice(0, end))
      this.buffer = this.buffer.slice(end + 2)
      if (frame) frames.push(frame)
    }
    return frames
  }

  /**
   * Emit a final frame from an unterminated tail (a server that closed the
   * connection without the last blank line). Idempotent: the buffer is cleared.
   */
  flush(): SseFrame[] {
    const tail = this.buffer
    this.buffer = ''
    if (!tail.trim()) return []
    const frame = parseFrameBlock(tail.replace(/\r\n?/g, '\n'))
    return frame ? [frame] : []
  }

  /** Bytes still buffered — a test seam for the split-frame case. */
  get pending(): string {
    return this.buffer
  }

  private normaliseLineEndings(): void {
    // Defer a trailing `\r`: its `\n` may still be in the next chunk.
    const deferred = this.buffer.endsWith('\r')
    const head = deferred ? this.buffer.slice(0, -1) : this.buffer
    this.buffer = head.replace(/\r\n?/g, '\n') + (deferred ? '\r' : '')
  }
}

/** `JSON.parse` that answers `undefined` instead of throwing on a bad frame. */
function parseFrameData(data: string): unknown {
  try {
    return JSON.parse(data) as unknown
  } catch {
    return undefined
  }
}

/* ------------------------------------------------------------ streaming --- */

/** Thrown when the research POST answers non-2xx (`401`, `404`, `409`). */
export class ResearchRequestError extends Error {
  constructor(readonly status: number, message?: string) {
    super(message ?? `POST research failed with status ${status}`)
    this.name = 'ResearchRequestError'
  }
}

/** Thrown when the response was `200` but carried no readable event stream. */
export class ResearchStreamError extends Error {
  constructor(message = 'research response carried no event stream') {
    super(message)
    this.name = 'ResearchStreamError'
  }
}

/** Per-frame callbacks. Each receives an already-narrowed value, never `unknown`. */
export interface ResearchHandlers {
  readonly onStatus?: (status: ResearchStatus) => void
  readonly onSite?: (site: SiteRecordV1) => void
  readonly onSummary?: (summary: ResearchSummary) => void
  readonly onDone?: (areaId: string | null) => void
}

export interface StreamResearchOptions {
  readonly request: ResearchRequest
  readonly endpoint?: string
  /** Injectable for tests; defaults to the global `fetch`. */
  readonly fetchImpl?: typeof fetch
  readonly token?: string | null
  readonly signal?: AbortSignal
  readonly handlers?: ResearchHandlers
}

/**
 * Run one research pass and consume its SSE stream to completion.
 *
 * @returns the `summary` frame, or `null` if the stream ended without one —
 * which the caller must treat as "incomplete", not as "nothing found".
 */
export async function streamResearch(
  options: StreamResearchOptions,
): Promise<ResearchSummary | null> {
  const { request, endpoint, fetchImpl = globalThis.fetch, token, signal, handlers = {} } = options

  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetchImpl(researchPath(request.area_id, endpoint), {
    method: 'POST',
    headers,
    body: JSON.stringify({ force_refresh: request.force_refresh }),
    ...(signal ? { signal } : {}),
  })
  if (!response.ok) throw new ResearchRequestError(response.status)
  if (!response.body) throw new ResearchStreamError()

  // Held on an object so the assignment inside `dispatch` survives TS's
  // control-flow analysis of the closure.
  const state: { summary: ResearchSummary | null } = { summary: null }

  const dispatch = (frame: SseFrame): void => {
    const data = parseFrameData(frame.data)
    switch (frame.event) {
      case 'status': {
        const status = sanitiseResearchStatus(data)
        if (status) handlers.onStatus?.(status)
        break
      }
      case 'site': {
        // Same gate as `GET /sites`: an unstamped record is dropped, not shown.
        const site = sanitiseSite(data)
        if (site) handlers.onSite?.(site)
        break
      }
      case 'summary': {
        const summary = sanitiseResearchSummary(data)
        state.summary = summary
        handlers.onSummary?.(summary)
        break
      }
      case 'done': {
        handlers.onDone?.(sanitiseResearchDone(data))
        break
      }
      default:
        break // an event this client does not know is ignored, never guessed at
    }
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseFrameParser()
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) dispatch(frame)
    }
    for (const frame of parser.push(decoder.decode())) dispatch(frame)
    for (const frame of parser.flush()) dispatch(frame)
  } finally {
    reader.releaseLock()
  }

  return state.summary
}

/* ------------------------------------------------------------- progress --- */

/**
 * What the surface is showing.
 *
 * `partial` and `incomplete` exist so a degraded or truncated pass can never be
 * displayed as `complete` (FR-012); `empty` is the honest zero-result state
 * (SC-006).
 *
 * `cancelled` is a **fifth non-success**, and separate from `error` on purpose: the user
 * stopped it, nothing failed, and — because `api/areas.py` commits only *after* the
 * stream completes and rolls back otherwise — nothing was written either. Folding it
 * into `error` would report the user's own decision as a fault, and would leave them
 * unsure whether a half-written pass is now in the commons.
 */
export type ResearchState =
  | 'idle'
  | 'running'
  | 'complete'
  | 'partial'
  | 'incomplete'
  | 'empty'
  | 'error'
  | 'cancelled'

/**
 * Did this rejection come from an abort?
 *
 * Structural, not `instanceof DOMException`: a `fetch` implementation injected by a
 * caller (or a test) rejects with whatever it likes, and the one thing every abort in
 * the platform agrees on is `name === 'AbortError'`.
 */
function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'AbortError'
  )
}

/** One progress line, built only from the fields the frame actually carried. */
export function describeStatus(status: ResearchStatus): string {
  const parts: string[] = [status.phase]
  if (status.source) parts.push(status.source)
  if (status.found !== null) parts.push(`${status.found} found`)
  if (status.degraded) parts.push('degraded')
  if (status.msg) parts.push(status.msg)
  return parts.join(' · ')
}

export interface ResearchProgressOptions extends ElapsedOptions {
  /** Forwarded for every stamped `site` frame, so a caller can render early. */
  readonly onSite?: (site: SiteRecordV1) => void
}

/**
 * The research progress surface (ux-handoff Screen 2, "Research & collect"): a
 * pulsing `RESEARCHING…` chip, a status line naming the current phase, a
 * per-source coverage list, and a result line.
 *
 * The mock's hand-drawn schematic map and its bar-chart coverage strip are M2
 * (`ux-handoff/INTEGRATION.md` § Staging) — this is the M1 form of the same
 * information: same fields, standard controls.
 */
export class ResearchProgressSurface {
  private readonly root: HTMLElement
  private readonly chip: HTMLParagraphElement
  private readonly elapsedLine: HTMLParagraphElement
  private readonly status: HTMLParagraphElement
  private readonly count: HTMLParagraphElement
  private readonly sources: HTMLUListElement
  private readonly result: HTMLParagraphElement
  private readonly cancelButton: HTMLButtonElement
  private readonly sourceRows = new Map<string, HTMLLIElement>()
  private readonly elapsed: ElapsedTimer
  /** What {@link cancel} aborts. Set per pass by {@link start}; `null` = not cancellable. */
  private canceller: (() => void) | null = null

  private state: ResearchState = 'idle'
  private siteCount = 0
  private summary: ResearchSummary | null = null

  constructor(
    private readonly container: HTMLElement,
    private readonly options: ResearchProgressOptions = {},
  ) {
    this.root = document.createElement('section')
    this.root.className = 'siyur-research'
    this.root.dataset.state = 'idle'
    this.root.setAttribute('aria-live', 'polite')
    this.root.hidden = true

    this.chip = document.createElement('p')
    this.chip.className = 'siyur-research__chip'
    this.chip.textContent = 'RESEARCHING…'

    /**
     * The elapsed line (UX-13). `aria-hidden`, deliberately: the whole surface is an
     * `aria-live="polite"` region, so a figure that changes every second would have a
     * screen reader announcing a number over the phase changes that actually carry the
     * news. It is a *reassurance* for someone watching a slow pass, and the phase line
     * beside it is the announcement.
     */
    this.elapsedLine = document.createElement('p')
    this.elapsedLine.className = 'siyur-research__elapsed'
    this.elapsedLine.setAttribute('aria-hidden', 'true')
    this.elapsedLine.hidden = true
    this.elapsed = new ElapsedTimer((text) => {
      this.elapsedLine.textContent = `${text} elapsed`
    }, options)

    this.status = document.createElement('p')
    this.status.className = 'siyur-research__status'

    this.count = document.createElement('p')
    this.count.className = 'siyur-research__count'

    this.sources = document.createElement('ul')
    this.sources.className = 'siyur-research__sources'

    this.result = document.createElement('p')
    this.result.className = 'siyur-research__result'

    /**
     * Stop it. A pass can run for minutes against a single-worker API, and until this
     * button existed the only ways out were reloading the tab or waiting.
     *
     * The listener is bound once and reads {@link canceller} at click time, so a surface
     * whose pass is not cancellable (a caller that supplied its own `signal`) fires
     * nothing — the button is hidden in that case, and hidden **and** inert rather than
     * hidden alone.
     */
    this.cancelButton = document.createElement('button')
    this.cancelButton.type = 'button'
    this.cancelButton.className = 'siyur-research__cancel'
    this.cancelButton.dataset.action = 'cancel-research'
    this.cancelButton.textContent = 'Stop this research pass'
    this.cancelButton.hidden = true
    this.cancelButton.addEventListener('click', () => this.cancel())

    this.root.append(
      this.chip,
      this.elapsedLine,
      this.status,
      this.count,
      this.sources,
      this.result,
      this.cancelButton,
    )
    this.container.append(this.root)
  }

  /** The mounted element (test/debug seam). */
  get element(): HTMLElement {
    return this.root
  }

  /** The surface's current state — the thing tests should assert on. */
  get currentState(): ResearchState {
    return this.state
  }

  /**
   * Begin a pass: clear the previous result so nothing stale is left on screen.
   *
   * @param cancel - how to abort the request behind this pass. Given one, the surface
   * offers a cancel control; without one it offers none, because a "stop" that only
   * changes the screen while the server keeps working is the same class of lie as a
   * state message that contradicts its own attribute.
   */
  start(cancel?: () => void): void {
    this.setState('running')
    this.root.hidden = false
    this.chip.hidden = false
    this.siteCount = 0
    this.summary = null
    this.sourceRows.clear()
    this.sources.replaceChildren()
    this.status.textContent = ''
    this.count.textContent = ''
    this.result.textContent = ''
    delete this.root.dataset.phase
    this.canceller = cancel ?? null
    this.cancelButton.hidden = !cancel
    this.cancelButton.disabled = false
    this.cancelButton.textContent = 'Stop this research pass'
    this.elapsedLine.hidden = false
    this.elapsed.start()
  }

  /**
   * Ask for the pass to stop.
   *
   * The **screen does not jump to "cancelled" here.** Aborting rejects the in-flight
   * `fetch`, which lands in {@link fail}, which is where the terminal state is set — one
   * place, so the surface cannot report a cancellation that did not happen. Until then
   * the button says what it is doing, which is `Stopping…`, not nothing.
   */
  cancel(): void {
    if (this.state !== 'running' || !this.canceller) return
    this.cancelButton.disabled = true
    this.cancelButton.textContent = 'Stopping…'
    this.canceller()
  }

  /** Handlers to hand to {@link streamResearch}. */
  handlers(): ResearchHandlers {
    return {
      onStatus: (status) => this.onStatus(status),
      onSite: (site) => this.onSite(site),
      onSummary: (summary) => {
        this.summary = summary
      },
      onDone: () => this.finish(),
    }
  }

  private onStatus(status: ResearchStatus): void {
    this.root.dataset.phase = status.phase
    this.status.textContent = describeStatus(status)
    if (status.source) {
      this.upsertSource(status.source, status.found, status.degraded)
    }
  }

  private onSite(site: SiteRecordV1): void {
    this.siteCount += 1
    this.count.textContent = `${this.siteCount} cited place${this.siteCount === 1 ? '' : 's'} so far`
    this.options.onSite?.(site)
  }

  /** One row per source, carrying that source's own count and degraded flag. */
  private upsertSource(source: string, found: number | null, degraded: boolean): void {
    let row = this.sourceRows.get(source)
    if (!row) {
      row = document.createElement('li')
      row.className = 'siyur-research__source'
      row.dataset.source = source
      this.sourceRows.set(source, row)
      this.sources.append(row)
    }
    row.dataset.degraded = String(degraded)
    if (found !== null) row.dataset.found = String(found)

    // "…" not "0": a frame with no count told us nothing about the count.
    const countText = found !== null ? String(found) : '…'
    row.textContent = degraded
      ? `${source} · ${countText} · degraded`
      : `${source} · ${countText}`
  }

  /**
   * End the pass and render the verdict. Idempotent, and safe to call after
   * {@link fail} — an errored pass keeps its error state.
   */
  finish(): void {
    if (this.state !== 'running') return
    this.chip.hidden = true
    this.stopCounting()

    const summary = this.summary
    if (!summary) {
      // The stream stopped talking. That is not "nothing found".
      this.setState('incomplete')
      this.result.textContent =
        'Research ended without a summary — the result may be incomplete.'
      return
    }
    if (!summary.readable) {
      this.setState('incomplete')
      this.result.textContent =
        'Research ended with an unreadable summary — totals unknown.'
      return
    }

    for (const [source, found] of Object.entries(summary.sources)) {
      this.upsertSource(source, found, summary.degraded_sources.includes(source))
    }

    if (summary.sites === 0) {
      // SC-006: an empty area says so. No placeholder place is ever invented.
      this.setState('empty')
      this.result.textContent = summary.degraded_sources.length
        ? `Nothing found here — and this pass was partial (degraded: ${summary.degraded_sources.join(', ')}).`
        : 'Nothing found here.'
      return
    }

    const totals =
      `${summary.sites} cited place${summary.sites === 1 ? '' : 's'} · ` +
      `${summary.new} new · ${summary.reused} reused · ` +
      `${summary.conflicts} conflict${summary.conflicts === 1 ? '' : 's'}`

    if (summary.degraded_sources.length) {
      // FR-012: partial is shown as partial, with the sources that failed named.
      this.setState('partial')
      this.result.textContent = `Partial result — ${totals}. Degraded: ${summary.degraded_sources.join(', ')}.`
      return
    }

    this.setState('complete')
    this.result.textContent = totals
  }

  /** The pass failed outright (transport, `401`, `409`) — or the user stopped it. */
  fail(error: unknown): void {
    this.chip.hidden = true
    this.root.hidden = false
    this.stopCounting()
    if (isAbortError(error)) {
      // Not a failure, and specifically not a half-written commons: `api/areas.py` commits
      // **after** the stream completes and rolls back otherwise, so a client that stops
      // mid-stream leaves the data exactly as it was. Saying so is the difference between
      // a user who tries again and one who wonders what they broke.
      this.setState('cancelled')
      this.result.textContent =
        'Research stopped at your request. Nothing from this pass was saved, so the ' +
        'commons is unchanged.'
      return
    }
    this.setState('error')
    const reason = error instanceof Error ? error.message : String(error)
    this.result.textContent = `Research could not complete: ${reason}`
  }

  /** Freeze the elapsed figure and retire the cancel control. */
  private stopCounting(): void {
    this.elapsed.stop()
    // Repainted once on the way out so the final figure is the pass's real duration and
    // not whatever the last one-second tick happened to show.
    this.elapsed.paint()
    this.canceller = null
    this.cancelButton.hidden = true
  }

  private setState(state: ResearchState): void {
    this.state = state
    this.root.dataset.state = state
  }

  destroy(): void {
    // An interval on a removed surface repaints a detached node once a second, forever.
    this.elapsed.stop()
    this.root.remove()
  }
}

/** Create and mount a {@link ResearchProgressSurface}. */
export function mountResearchProgress(
  container: HTMLElement,
  options: ResearchProgressOptions = {},
): ResearchProgressSurface {
  return new ResearchProgressSurface(container, options)
}

/**
 * Run a pass and drive a surface with it — the whole of "trigger research" in
 * one call, always ending in a terminal state (`finish` on any exit path).
 *
 * **The pass gets an `AbortController` unless the caller brought its own signal.** That
 * is what makes the surface's cancel control real rather than cosmetic: the button
 * aborts the `fetch`, the rejection lands in `surface.fail`, and the server sees the
 * disconnect and rolls its transaction back. A caller that supplies `options.signal`
 * keeps ownership of cancellation and the surface offers no button, because two things
 * cancelling one request is a race with a wrong answer available.
 */
export async function runResearch(
  surface: ResearchProgressSurface,
  options: StreamResearchOptions,
): Promise<ResearchSummary | null> {
  const controller = options.signal ? null : new AbortController()
  surface.start(controller ? () => controller.abort() : undefined)
  try {
    const summary = await streamResearch({
      ...options,
      ...(controller ? { signal: controller.signal } : {}),
      handlers: surface.handlers(),
    })
    surface.finish()
    return summary
  } catch (error) {
    surface.fail(error)
    return null
  }
}
