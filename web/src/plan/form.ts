/**
 * The plan request form — area, time budget, walking budget, interests, **date** and
 * day start (FR-001, `contracts/plans.md` § POST /plans).
 *
 * ────────────────────────────────────────────────────────────────────────────────
 * ## `date` is required and is NEVER defaulted here. This is the whole reason the
 * ## field exists as a field.
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * The contract already forbids defaulting it server-side; defaulting it *client*-side
 * is the same bug wearing a different hat, and a worse one, because the browser has a
 * clock that looks authoritative. `new Date().toISOString().slice(0, 10)` is a **UTC**
 * calendar day: at 10:00 on a Monday in `Pacific/Auckland` (UTC+13) it yields Sunday,
 * and every stop is then checked against Sunday opening hours — a closed place reported
 * as open, in the direction that sends a traveller to a locked door. The local-time
 * variants are no better: the *browser's* calendar day is not the **area's** calendar
 * day either, and the area is the frame `opening-hours-py` evaluates in.
 *
 * So the date input ships with **no value**, {@link validatePlanRequest} refuses an
 * empty one by name, and nothing in `src/plan/` constructs a `Date` at all. The last of
 * those is asserted structurally by a source scan in `web/test/plan.test.ts` — the
 * mechanism, not the intention.
 *
 * `day_start` may be defaulted and is: it is a wall-clock anchor **inside** the area's
 * own frame, so a default shifts when the day starts, not which day it is or which
 * opening rules apply. `hours`/`walking_m` are preferences, likewise safe. The
 * asymmetry is the point — only the calendar frame carries the hazard.
 */

import type { ProposeRequest } from './types'

/** Raw string values, exactly as the DOM holds them. */
export interface PlanFormValues {
  readonly area_id: string
  readonly hours: string
  readonly walking_m: string
  readonly interests: string
  readonly date: string
  readonly day_start: string
  readonly lang?: string
}

/** One rejected field, named so the surface can point at it. */
export interface PlanFormProblem {
  readonly field: keyof PlanFormValues
  readonly message: string
}

export type PlanFormResult =
  | { readonly ok: true; readonly request: ProposeRequest }
  | { readonly ok: false; readonly problems: readonly PlanFormProblem[] }

/** Safe to default: an anchor within the area's own frame (see the module header). */
export const DEFAULT_DAY_START = '10:00'
export const DEFAULT_HOURS = '4'
export const DEFAULT_WALKING_M = '4000'

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const WALL_CLOCK = /^\d{2}:\d{2}$/

const positive = (raw: string): number | null => {
  const value = Number(raw.trim())
  return Number.isFinite(value) && value > 0 ? value : null
}

/**
 * Validate the form's raw strings into a `POST /plans` body.
 *
 * Mirrors the contract's own `422` conditions (non-positive budgets, unparseable
 * `day_start`, missing or unparseable `date`) so the user is told which field is wrong
 * instead of being handed a server rejection — the server stays authoritative either
 * way; this is not a substitute for it.
 */
export function validatePlanRequest(values: PlanFormValues): PlanFormResult {
  const problems: PlanFormProblem[] = []

  const areaId = values.area_id.trim()
  if (!areaId) {
    problems.push({ field: 'area_id', message: 'Choose an area on the map first.' })
  }

  const hours = positive(values.hours)
  if (hours === null) {
    problems.push({ field: 'hours', message: 'How many hours do you have? Must be more than 0.' })
  }

  const walking = positive(values.walking_m)
  if (walking === null) {
    problems.push({
      field: 'walking_m',
      message: 'How far will you walk, in metres? Must be more than 0.',
    })
  }

  // No default, no fallback, no clock. See the module header.
  const date = values.date.trim()
  if (!date) {
    problems.push({
      field: 'date',
      message:
        'Pick the date you are planning for — it decides which opening hours and ' +
        'public holidays apply.',
    })
  } else if (!ISO_DATE.test(date)) {
    problems.push({ field: 'date', message: 'The date must be YYYY-MM-DD.' })
  }

  const dayStart = values.day_start.trim()
  if (!WALL_CLOCK.test(dayStart)) {
    problems.push({ field: 'day_start', message: 'The day start must be HH:MM, area-local.' })
  }

  if (problems.length > 0 || hours === null || walking === null) {
    return { ok: false, problems }
  }
  return {
    ok: true,
    request: {
      area_id: areaId,
      budgets: { walking_m: walking, hours },
      // Free-form and unedited: this is the model's only free input.
      interests: values.interests.trim(),
      date,
      day_start: dayStart,
      lang: values.lang?.trim() || 'en',
    },
  }
}

/* ------------------------------------------------------------- the form --- */

export interface PlanFormOptions {
  /** Called only from `submit`, with a validated body. */
  readonly onSubmit?: (request: ProposeRequest) => void | Promise<void>
  /** Called when `onSubmit` rejects. The form states the failure either way. */
  readonly onSubmitFailure?: (error: unknown) => void
  readonly lang?: string
}

function field(
  form: HTMLFormElement,
  name: keyof PlanFormValues,
  labelText: string,
  input: HTMLInputElement | HTMLTextAreaElement,
): void {
  const wrapper = document.createElement('label')
  wrapper.className = 'siyur-plan-form__field'
  wrapper.dataset.field = name
  const label = document.createElement('span')
  label.className = 'siyur-plan-form__label'
  label.textContent = labelText
  input.name = name
  input.className = 'siyur-plan-form__input'
  wrapper.append(label, input)
  form.append(wrapper)
}

function textInput(type: string, attrs: Record<string, string> = {}): HTMLInputElement {
  const input = document.createElement('input')
  input.type = type
  for (const [key, value] of Object.entries(attrs)) input.setAttribute(key, value)
  return input
}

/**
 * The mounted request form.
 *
 * The area is **shown, not typed**: it comes from the resolved area the user delimited
 * on the map (`../map/areas.ts`), so the input is read-only and set through
 * {@link setArea}. A UUID is not something a person enters.
 */
export class PlanRequestForm {
  private readonly form: HTMLFormElement
  private readonly problems: HTMLUListElement
  /**
   * Where a *transport* failure is said — separate from {@link problems}, which is a
   * list of fields the user can fix. An expired session is not a bad field.
   */
  private readonly failure: HTMLParagraphElement
  private pending = false

  constructor(
    container: HTMLElement,
    private readonly options: PlanFormOptions = {},
  ) {
    this.form = document.createElement('form')
    this.form.className = 'siyur-plan-form'
    this.form.noValidate = true

    const area = textInput('text', { readonly: 'readonly' })
    field(this.form, 'area_id', 'Area', area)
    const hours = textInput('number', { min: '0.25', step: '0.25' })
    const walking = textInput('number', { min: '100', step: '100' })
    field(this.form, 'hours', 'Time budget (hours)', hours)
    field(this.form, 'walking_m', 'Walking budget (metres)', walking)

    const interests = document.createElement('textarea')
    interests.rows = 2
    interests.placeholder = 'art and coffee, nothing crowded'
    field(this.form, 'interests', 'Interests', interests)

    // ⬇︎ No `value`. Not now, not on mount, not from a clock. See the module header.
    field(this.form, 'date', 'Date', textInput('date', { required: 'required' }))
    field(this.form, 'day_start', 'Day starts (area-local)', textInput('time'))

    this.setValue('hours', DEFAULT_HOURS)
    this.setValue('walking_m', DEFAULT_WALKING_M)
    this.setValue('day_start', DEFAULT_DAY_START)

    this.problems = document.createElement('ul')
    this.problems.className = 'siyur-plan-form__problems'
    this.problems.setAttribute('aria-live', 'polite')

    this.failure = document.createElement('p')
    this.failure.className = 'siyur-plan-form__failure'
    this.failure.setAttribute('role', 'alert')
    this.failure.hidden = true

    const submit = document.createElement('button')
    submit.type = 'submit'
    submit.className = 'siyur-plan-form__submit'
    submit.textContent = 'Plan this day →'

    this.form.append(this.problems, this.failure, submit)
    this.form.addEventListener('submit', (event) => {
      event.preventDefault()
      // `submit()` handles its own rejection, so nothing is discarded here — see the
      // note on {@link PlanRequestForm.submit}.
      void this.submit()
    })
    container.append(this.form)
  }

  get element(): HTMLFormElement {
    return this.form
  }

  /** Bind the form to a resolved area (`AreaResolution.area_id`). */
  setArea(areaId: string): void {
    this.setValue('area_id', areaId)
  }

  /** The raw strings currently in the DOM. */
  values(): PlanFormValues {
    return {
      area_id: this.read('area_id'),
      hours: this.read('hours'),
      walking_m: this.read('walking_m'),
      interests: this.read('interests'),
      date: this.read('date'),
      day_start: this.read('day_start'),
      lang: this.options.lang ?? 'en',
    }
  }

  /**
   * Validate and, only if valid, hand the body to `onSubmit`.
   *
   * **A rejecting `onSubmit` is reported, not swallowed.** The problems list is cleared
   * before the request is made, so an expired session (`401` from `streamPlan`) used to
   * leave an empty list and an unchanged form: a submit that visibly did nothing, which
   * a user answers by submitting again. The failure is stated on its own line and the
   * button is released so they *can* retry — knowingly.
   *
   * @returns the **validation** decision. A transport failure does not change it (the
   * body was valid); it is reported on the form and, if the caller wants to act on it,
   * through `onSubmitFailure`.
   */
  async submit(): Promise<PlanFormResult> {
    const result = validatePlanRequest(this.values())
    this.problems.replaceChildren()
    this.clearFailure()
    if (!result.ok) {
      for (const problem of result.problems) {
        const item = document.createElement('li')
        item.className = 'siyur-plan-form__problem'
        item.dataset.field = problem.field
        item.textContent = problem.message
        this.problems.append(item)
      }
      return result
    }
    // One request at a time: a second click while the first is in flight would start a
    // second proposal for the same area, which the server answers `409` (contract,
    // idempotency guard) — a refusal the user did nothing to deserve.
    if (this.pending) return result
    this.setPending(true)
    try {
      await this.options.onSubmit?.(result.request)
    } catch (error: unknown) {
      this.showFailure(error)
      this.options.onSubmitFailure?.(error)
    } finally {
      this.setPending(false)
    }
    return result
  }

  destroy(): void {
    this.form.remove()
  }

  private setPending(pending: boolean): void {
    this.pending = pending
    this.form.dataset.pending = String(pending)
    const submit = this.form.querySelector<HTMLButtonElement>('.siyur-plan-form__submit')
    if (submit) submit.disabled = pending
  }

  private clearFailure(): void {
    this.failure.textContent = ''
    this.failure.hidden = true
    delete this.failure.dataset.status
  }

  /**
   * State the failure without inventing a cause.
   *
   * A `PlanRequestError` is recognised by its `status` — checked structurally rather
   * than with `instanceof`, so this module does not pull the SSE transport in behind an
   * import purely to name a number. Anything else is reported as not having completed,
   * which is the only thing that is actually known.
   */
  private showFailure(error: unknown): void {
    const status = (error as { status?: unknown } | null | undefined)?.status
    const known = typeof status === 'number'
    this.failure.textContent = known
      ? status === 401
        ? 'Your session has ended, so no day was planned. Sign in and try again.'
        : `The server refused this request (status ${status}). No day was planned.`
      : 'The request did not complete, so no day was planned. Try again.'
    if (known) this.failure.dataset.status = String(status)
    this.failure.hidden = false
  }

  private control(name: keyof PlanFormValues): HTMLInputElement | HTMLTextAreaElement | null {
    return this.form.querySelector(`[name="${name}"]`)
  }

  private read(name: keyof PlanFormValues): string {
    return this.control(name)?.value ?? ''
  }

  private setValue(name: keyof PlanFormValues, value: string): void {
    const control = this.control(name)
    if (control) control.value = value
  }
}

/** Create and mount a {@link PlanRequestForm}. */
export function mountPlanForm(
  container: HTMLElement,
  options: PlanFormOptions = {},
): PlanRequestForm {
  return new PlanRequestForm(container, options)
}
