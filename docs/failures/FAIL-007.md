# FAIL-007 — A relative sprite URL MapLibre refuses, behind a green test that asserted the relative URL

- Date: 2026-08-07 · Severity: low
- Root-cause class: other (library precondition — one asset URL held to a stricter rule than its neighbours, **and a test that pinned the wrong side of it**)

## Symptom

The dev basemap renders streets, water and labels. It has no sprite-backed icons, and the browser
console carries one error on every page load:

```
Error: Invalid sprite URL "/basemap/sprites/dark", must be absolute.
  at Fi._loadSprite (maplibre-gl.js:26547)
  at Fi._load       (maplibre-gl.js:26488)
```

Every asset behind that path is present and served:

```
GET /basemap/sprites/dark.json            → 200
GET /basemap/glyphs/Noto Sans Regular/…   → 200
HEAD /basemap/area.pmtiles                → 200
map.isStyleLoaded()                       → true
map.getStyle().layers.length              → 71
```

So nothing is missing, nothing 404s, the style loads, and 71 layers draw. **MapLibre requires
`style.sprite` to be absolute** and rejects a root-relative path that every other part of the app —
including `style.glyphs`, three lines above it — accepts happily. `glyphs` is a URL *template*
resolved per fontstack and carries no such rule, which is why the asymmetry reads as arbitrary
until you hit it.

## Why it survived

**The unit test asserted the bug.** `web/test/basemap.test.ts` pinned the exact relative string the
implementation produced:

```ts
expect(basemapStyle({ archiveUrl: RHODES }).sprite).toBe('/basemap/sprites/dark')
```

That is green, and it is green *because* the value is relative — the one property that makes
MapLibre throw. The test asserted **what the implementation did**, not **what the consumer
requires**, so it locked the defect in rather than catching it. 177 web tests, CI jobs 1–8 green,
and a basemap that never loaded a sprite.

It also failed quietly at runtime, which is what kept it out of view: the style still loads and
tiles still draw, so the map looks correct at a glance. Only icons are missing, and only the
console says so. PR #72's stated goal — "a dev vector basemap so the map shows streets" — was
genuinely met, which made the partial failure easy to read as success.

Found by opening the app in a real browser and reading the console, not by any gate.

## Fix

`web/src/map/basemap.ts` gains `absoluteAssetUrl()`, resolving the sprite path against
`location.origin` (falling back to the relative path where there is no `location`, i.e. a
non-browser import, which never fetches a sprite anyway). `glyphs` is deliberately left relative —
it is a template, MapLibre imposes no such rule on it, and changing it would be a change with no
defect behind it.

## Regression guard (Constitution Article IV)

`web/test/basemap.test.ts` now asserts the **property**, not the string:

```ts
expect(() => new URL(sprite)).not.toThrow()      // it parses as absolute
expect(sprite).toMatch(/^https?:\/\//)           // MapLibre's actual requirement
expect(new URL(sprite).origin).toBe(location.origin)  // still vendored, not remote
```

The origin assertion keeps the "no remote host" guarantee the original test was really protecting,
so making the URL absolute cannot quietly become making it point somewhere else.

## The transferable lesson

**A test that asserts a literal produced by the implementation cannot fail when the implementation
is wrong** — it can only fail when the implementation *changes*. This one pinned
`'/basemap/sprites/dark'` because that is what the code emitted, and MapLibre's requirement was
never expressed anywhere in the suite. Where a value must satisfy an external consumer's rule,
assert the rule.

Two related gaps this sits next to, both already recorded rather than found here: the glyph ranges
are pruned to U+0000–U+04FF, so a Hebrew/Arabic/CJK area renders labels as *nothing at all*
(`tasks.md` T035a, and the same silent-partial-render shape as this entry); and `TileSourceV1.glyphs`
carries no `sha256` while every other bundled artifact does (T035b).
