/**
 * T051 — the travel surface: itinerary, timeline, per-place info and narration,
 * rendered **from the bundle alone**.
 *
 * ────────────────────────────────────────────────────────────────────────────────
 * ## ADR-0019 revisit trigger 1 fires here. This is the mechanism, stated.
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * ADR-0019 was ratified on the value-scoped reading of FR-004: *a value's text never
 * reaches any surface without its `source + license` stamp in the same element, in the
 * same frame.* Its revisit trigger is **"the first surface that renders values with no
 * interaction available at all"** — and that surface is this one. Offline there is no
 * hover, no focus peek, no popup-on-demand for attribution to move to, so **co-presence
 * must be discharged by layout**. The ADR's amendment says whoever implements T051 owes
 * that decision explicitly rather than discovering it. So:
 *
 * **1. Every commons-derived value renders through `renderSourcedValue` — an inline
 *    chip, in the same element, always.** Place names, addresses, opening hours,
 *    narration text, and a leg's distance and duration all go through the single funnel
 *    in `../map/attribution-chip.ts`. There is no second path from a sourced value to
 *    the DOM in this package. That is co-presence discharged by layout, per value,
 *    with no interaction required — which is precisely what the trigger asked for.
 *
 * **2. `ATTRIBUTION.md` does not discharge it and is not used for it.** The bundle's
 *    regenerated `ATTRIBUTION.md` is an **aggregate** credit — it establishes that the
 *    bundle contains OSM-derived data and credits each contributing article. It does
 *    not establish that *this rendered value* carries *its own* stamp. It is rendered
 *    by {@link createBundleCredit} as what it is (the bundle's licence page), clearly
 *    additional to the per-value chips rather than a substitute for them.
 *
 * **3. Plan structure carries no chip, and the surface says so rather than leaving a
 *    gap.** Stop times, stop order and dwell are the *user's own composition*:
 *    `ItineraryV1` is user-owned private data (`docs/data/itinerary.md`) and carries no
 *    `SourceRef` on any of those fields — there is no stamp to render, and inventing
 *    one would be exactly the "no default string" failure `attribution-chip.ts` exists
 *    to prevent. {@link createPlanCredit} therefore states in words that times and
 *    order are the traveller's own plan, so an absent chip is *explained* rather than
 *    indistinguishable from a missing one.
 *
 * Point 3 is a judgement call about the scope of "value" and it owes an ADR. It is
 * recorded here so a reviewer can disagree with a decision rather than discover a gap.
 *
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * **Times are area-local wall clock, rendered verbatim.** `planned_start` is a frozen
 * local `HH:MM` on the plan's `date`, and the traveller's device clock is explicitly
 * not required to match it. Nothing here constructs a `Date` from a plan time: a device
 * still on the departure timezone — the normal state of a phone that just landed —
 * would shift the entire day by hours, silently and plausibly.
 */

import { renderSourcedValue } from '../map/attribution-chip'
import type { SiteRecordV1, SourceRef, SourcedValue } from '../map/types'
import type { WithheldEntry } from '../bundle/types'
import type { ItineraryV1, RouteLegV1, Stop, Story } from './types'
import { createWithheldBlock } from './withheld'

/**
 * Adapt a bare-`SourceRef` fact into the `SourcedValue` shape the chip funnel takes.
 *
 * `Story` and `RouteLegV1` carry a bare `SourceRef` and no `bundleable` field — the
 * quarantine filter *derives* their bundleability at compile time (`poi-site.md`,
 * `route-leg.md`). By the time the value is inside a bundle the answer is settled and
 * observable: it is here, so it was bundleable. `bundleable: true` is therefore a fact
 * about this artifact, not a default this module invented.
 */
function stamped<T>(value: T, source: SourceRef): SourcedValue<T> {
  return { value, source, bundleable: true }
}

/** The site's display name in `lang`, falling back to any name it does carry. */
function nameValue(site: SiteRecordV1, lang: string): SourcedValue<string> | null {
  return site.names[lang] ?? Object.values(site.names)[0] ?? null
}

function heading(level: 'h2' | 'h3', text: string, className: string): HTMLElement {
  const element = document.createElement(level)
  element.className = className
  element.textContent = text
  return element
}

/** One adapted CC-BY-SA story: its text and its article credit, co-present. */
export function renderStory(story: Story, lang: string): HTMLElement | null {
  const text = story.text_by_lang[lang] ?? Object.values(story.text_by_lang)[0]
  if (!text) return null
  const article = document.createElement('article')
  article.className = 'siyur-story'
  // Through the funnel, like every other value. An unstamped story yields `null` from
  // `renderSourcedValue` and therefore renders nothing at all — which is the correct
  // outcome for adapted CC-BY-SA prose we cannot credit (SC-010: zero stories without
  // attribution).
  const rendered = renderSourcedValue(stamped(text, story.source), {
    className: 'siyur-story__text',
  })
  if (!rendered) return null
  article.append(rendered)
  return article
}

/** A walking leg's distance and time, each carrying the leg's ODbL stamp. */
export function renderLeg(leg: RouteLegV1): HTMLElement {
  const row = document.createElement('li')
  row.className = 'siyur-leg'
  row.dataset.legId = leg.id
  const distance = renderSourcedValue(stamped(leg.distance_m, leg.source), {
    label: 'WALK',
    format: (metres) => `${Math.round(metres)} m`,
  })
  const duration = renderSourcedValue(stamped(Math.round(leg.duration_s / 60), leg.source), {
    format: (minutes) => `${minutes} min`,
  })
  if (distance) row.append(distance)
  if (duration) row.append(duration)
  return row
}

export interface PlaceCardOptions {
  readonly site: SiteRecordV1
  readonly stories: readonly Story[]
  /** This site's entries from `BundleManifestV1.withheld`. */
  readonly withheld: readonly WithheldEntry[]
  readonly lang: string
}

/**
 * The per-place card: name, address, hours, narration, and the "needs connectivity"
 * block for anything quarantine removed.
 *
 * Returns `null` for a site with no renderable name — a card headed by nothing is not
 * information, and the site is unidentifiable to the traveller anyway.
 */
export function createPlaceCard(options: PlaceCardOptions): HTMLElement | null {
  const { site, stories, withheld, lang } = options
  const name = nameValue(site, lang)
  const rendered = name && renderSourcedValue(name, { className: 'siyur-place__name' })
  if (!rendered) return null

  const card = document.createElement('section')
  card.className = 'siyur-place'
  card.dataset.siteId = site.id
  card.append(rendered)

  const address = renderSourcedValue(site.address, { label: 'ADDRESS' })
  if (address) card.append(address)
  const hours = renderSourcedValue(site.opening_hours, { label: 'HOURS' })
  if (hours) card.append(hours)

  for (const story of stories) {
    const article = renderStory(story, lang)
    if (article) card.append(article)
  }

  // Rendered LAST, and as a block rather than inline: `withheld[].field` indices are
  // pre-removal and may not be used to position anything (see `./withheld`).
  const block = createWithheldBlock(withheld)
  if (block) card.append(block)

  return card
}

/**
 * The day's credit for plan structure — mechanism point 3 above.
 *
 * Not a chip: there is no `SourceRef` on a stop time to render one from. It states the
 * provenance of the times and the order in words, so an absent stamp is a stated fact
 * rather than an unexplained omission.
 */
export function createPlanCredit(itinerary: ItineraryV1): HTMLElement {
  const credit = document.createElement('p')
  credit.className = 'siyur-plan-credit'
  credit.dataset.planSource = 'user-owned'
  credit.textContent =
    `Times, stop order and dwell are your own plan for ${itinerary.date} ` +
    `(area-local time) — your data, not sourced data. Everything else on this page ` +
    `carries the source and licence of the value it belongs to.`
  return credit
}

/**
 * The bundle's aggregate licence page — `ATTRIBUTION.md`, verbatim.
 *
 * Labelled as an *aggregate* credit on purpose (mechanism point 2). It is legally
 * obligated content, hashed like every other artifact, and it is **not** the per-value
 * co-presence mechanism.
 */
export function createBundleCredit(attributionMarkdown: string): HTMLElement {
  const section = document.createElement('section')
  section.className = 'siyur-bundle-credit'
  section.append(heading('h2', 'Sources and licences in this bundle', 'siyur-bundle-credit__title'))
  const body = document.createElement('pre')
  body.className = 'siyur-bundle-credit__body'
  // `textContent`, never `innerHTML`: this string is compiled from source metadata and
  // article titles, which are third-party text.
  body.textContent = attributionMarkdown
  section.append(body)
  return section
}

export interface DayRenderOptions {
  readonly itinerary: ItineraryV1
  readonly sites: ReadonlyMap<string, SiteRecordV1>
  readonly narrations: ReadonlyMap<string, readonly Story[]>
  readonly withheld: ReadonlyMap<string, readonly WithheldEntry[]>
}

/**
 * The whole day: the timeline in plan order, each stop's place card, each leg's
 * distance and time.
 *
 * Timeline order comes from `timeline.entries` when the bundle carries them, and falls
 * back to `stops` order otherwise. Legs are looked up by `leg_id` and stops by
 * `stop_order` — the itinerary card's one addressing scheme, not two.
 */
export function renderDay(options: DayRenderOptions): HTMLElement {
  const { itinerary, sites, narrations, withheld } = options
  const legsById = new Map(itinerary.legs.map((leg) => [leg.id, leg]))
  const stopsByOrder = new Map(itinerary.stops.map((stop) => [stop.order, stop]))

  const root = document.createElement('div')
  root.className = 'siyur-day'
  root.dataset.itineraryId = itinerary.id
  root.dataset.date = itinerary.date
  root.append(heading('h2', itinerary.date, 'siyur-day__date'), createPlanCredit(itinerary))

  const list = document.createElement('ol')
  list.className = 'siyur-timeline'

  const entries = itinerary.timeline.length
    ? itinerary.timeline
    : itinerary.stops.map((stop) => ({
        stop_order: stop.order,
        leg_id: null,
        start: stop.planned_start,
        duration_min: stop.dwell_min,
      }))

  for (const entry of entries) {
    if (entry.leg_id !== null) {
      const leg = legsById.get(entry.leg_id)
      if (leg) list.append(renderLeg(leg))
      continue
    }
    const stop = entry.stop_order === null ? undefined : stopsByOrder.get(entry.stop_order)
    if (!stop) continue
    const item = renderStopItem(stop, entry.start, entry.duration_min, {
      sites,
      narrations,
      withheld,
      lang: itinerary.lang,
    })
    if (item) list.append(item)
  }

  root.append(list)
  return root
}

function renderStopItem(
  stop: Stop,
  start: string,
  durationMin: number,
  context: {
    sites: ReadonlyMap<string, SiteRecordV1>
    narrations: ReadonlyMap<string, readonly Story[]>
    withheld: ReadonlyMap<string, readonly WithheldEntry[]>
    lang: string
  },
): HTMLElement | null {
  const site = context.sites.get(stop.site_id)
  if (!site) return null
  const card = createPlaceCard({
    site,
    stories: context.narrations.get(stop.site_id) ?? [],
    withheld: context.withheld.get(stop.site_id) ?? [],
    lang: context.lang,
  })
  if (!card) return null

  const item = document.createElement('li')
  item.className = 'siyur-timeline__item'
  item.dataset.stopOrder = String(stop.order)

  const when = document.createElement('p')
  when.className = 'siyur-timeline__when'
  // Verbatim wall clock. No `Date`, no locale conversion — see the module header.
  when.textContent = `${start} · ${durationMin} min`
  item.append(when, card)
  return item
}
