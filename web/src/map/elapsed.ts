/**
 * How long something has been running — the missing half of every long operation in this
 * app (UX-13, F-05).
 *
 * A research pass sat on one frozen status label for **over nine minutes** while the API
 * held 99% CPU, and a name search says nothing for the **61.6 s** it takes to answer. In
 * both cases the user cannot tell working from hung, and the only information needed to
 * tell them apart is the one this module produces.
 *
 * ────────────────────────────────────────────────────────────────────────────────
 * ## This is a DURATION, and deliberately not a clock reading.
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * {@link monotonicNow} is `performance.now()` — a monotonic millisecond counter with no
 * calendar, no timezone and no locale. That matters twice over:
 *
 * 1. **Correctness.** `Date.now()` follows the system clock, so an NTP step or a manual
 *    change mid-pass makes the elapsed figure jump or go backwards. `performance.now()`
 *    cannot.
 * 2. **The rule the plan surfaces live under.** `src/plan/` may not construct a `Date`
 *    at all (a plan time is area-local wall clock; a browser `Date` re-dates it silently),
 *    and `test/plan.test.ts` enforces that with a source scan. Nothing here would violate
 *    that scan even if it were widened to `src/map/`: there is no `Date`, no `Intl`, no
 *    `toLocale*`. "12s" is the same string in every timezone on earth, because it is not
 *    a time — it is a length of time.
 */

/** The monotonic clock these surfaces count with. Injectable, so tests need no timers. */
export type MonotonicClock = () => number

/** `performance.now()`, with a `0` floor for the rare environment that lacks it. */
export const monotonicNow: MonotonicClock = () =>
  typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : 0

/**
 * A millisecond duration as the shortest legible string: `9s`, `59s`, `1m 05s`, `12m 30s`.
 *
 * Seconds are floored, never rounded up: "1s" while 400 ms have passed would be this
 * module inventing progress, which is the same failure the research surface refuses to
 * commit with its own counts. A negative delta (a clock that somehow ran backwards)
 * reads `0s` rather than a minus sign.
 */
export function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`
}

/** Options shared by the surfaces that count elapsed time. */
export interface ElapsedOptions {
  /** Injectable monotonic clock; defaults to {@link monotonicNow}. */
  readonly now?: MonotonicClock
  /** How often the displayed figure is refreshed, in ms. Defaults to 1000. */
  readonly tickMs?: number
}

/**
 * A running elapsed figure, rendered into `element` as text.
 *
 * Stopping **keeps the last figure on screen**: how long a pass took is worth as much
 * after it ends as during it — "that took 4m 12s" is what tells a user whether to try
 * again — and blanking it at the moment of completion is how the app forgets, again, to
 * say what happened.
 */
export class ElapsedTimer {
  private startedAt: number | null = null
  private timer: ReturnType<typeof setInterval> | null = null

  constructor(
    private readonly render: (text: string) => void,
    private readonly options: ElapsedOptions = {},
  ) {}

  /** Whether the timer is currently counting. */
  get running(): boolean {
    return this.timer !== null
  }

  /** Elapsed ms since {@link start}, or `0` if it has not been started. */
  get elapsedMs(): number {
    const now = this.options.now ?? monotonicNow
    return this.startedAt === null ? 0 : Math.max(0, now() - this.startedAt)
  }

  /** Begin (or restart) counting from zero, painting the first figure immediately. */
  start(): void {
    const now = this.options.now ?? monotonicNow
    this.stop()
    this.startedAt = now()
    this.paint()
    this.timer = setInterval(() => this.paint(), this.options.tickMs ?? 1000)
  }

  /** Stop counting. The last painted figure stays where it is. */
  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer)
      this.timer = null
    }
  }

  /** Repaint now — used by callers that finish between ticks. */
  paint(): void {
    this.render(formatElapsed(this.elapsedMs))
  }
}
