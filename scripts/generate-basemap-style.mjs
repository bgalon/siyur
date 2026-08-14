#!/usr/bin/env node
//
// Freeze the Protomaps basemap **layer list** into `data/basemap/`, so that
// `compiler/style.py` can assemble a bundle's `style/base.json` without a Node
// runtime and without a second, hand-copied transcription of 70-odd layers.
//
//   pnpm -C web install                     # the layers come from web/node_modules
//   node scripts/generate-basemap-style.mjs # rewrites data/basemap/protomaps-layers.json
//
// **Why a generator and not Python constants.** The layers are ~65 KB of paint and
// filter expressions per flavour, they change with every `@protomaps/basemaps`
// release, and `web/src/map/basemap.ts` already calls `layers()` for the dev map. A
// hand-port would be a second source of truth that drifts silently — the map still
// renders, just not the same map. So the npm package stays the single upstream and
// this script is the only thing that reads it (AGENTS.md: *fix the generator, never
// hand-edit the output*).
//
// **Determinism is a feature here**, not tidiness: the output is committed, so a
// regeneration that reorders keys would produce a diff that says "the basemap
// changed" when nothing did. Object keys are therefore sorted recursively (arrays
// are NOT — a MapLibre filter/expression is positional), the layout is fixed, and
// the file ends with a newline.
//
// **One line per layer**, rather than a pretty-printed tree. Pretty-printing this
// measures 584 KB / ~30,000 lines, and `data/` is not among the paths CI job 7's
// diff-guard excludes — a regeneration would blow the 500-line budget on machine
// output every time. One line per layer keeps `git diff` legible at the level a
// reviewer actually reads it ("which layers changed"), in ~150 lines.
//
// **The package version is recorded in the output** and pinned by
// `tests/test_compiler_style.py`. Bumping `@protomaps/basemaps` in `web/package.json`
// without re-running this script is the exact drift this arrangement invites; that
// test is what makes it visible instead of shipping a style built from a version the
// app no longer uses.
//
// Licence: the Protomaps style/pipeline is BSD-3-Clause (the *tile data* it renders
// is © OpenStreetMap contributors, ODbL-1.0 — credited by the map's attribution
// control and by every bundle's ATTRIBUTION.md). Both are in the bundleable set.

import { createRequire } from 'node:module'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const WEB_PACKAGE_JSON = path.join(REPO_ROOT, 'web', 'package.json')
const OUTPUT = path.join(REPO_ROOT, 'data', 'basemap', 'protomaps-layers.json')

const PACKAGE = '@protomaps/basemaps'

// Resolved against `web/package.json` rather than this file: bare specifiers resolve
// from the *importing module's* directory, and `scripts/` has no node_modules. The
// alternative — reaching into `web/node_modules/@protomaps/basemaps/dist/...` by
// path — would hardcode the package's internal layout, which its `exports` map exists
// precisely to keep private.
const require = createRequire(WEB_PACKAGE_JSON)
const { layers, namedFlavor } = require(PACKAGE)

/**
 * Style-internal id of the vector source, matching `web/src/map/basemap.ts` and
 * `compiler.style.BASEMAP_SOURCE_ID`. It is baked into every layer's `source`, so it
 * is a generation-time constant rather than something Python can rewrite cheaply.
 */
const SOURCE_ID = 'protomaps'

/**
 * Label language, as a BCP-47 primary subtag. The Protomaps schema falls back to the
 * feature's local `name` when a translation is absent, so `en` means "English where
 * the tiles have it, the place's own name otherwise" — a presentation default, not a
 * place-specific one (FR-001). Changing it is a regeneration, not a compile-time flag.
 */
const LANG = 'en'

/**
 * Both flavours, because `compiler.tiles.DEFAULT_SPRITE_ASSETS` vendors both sprite
 * sheets into every bundle. Freezing only one would leave half the bundled sprites
 * unreachable and make a light-mode style a regeneration rather than a parameter.
 */
const FLAVORS = ['light', 'dark']

/** Recursively sort object keys; leave arrays alone (expressions are positional). */
function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys)
  if (value === null || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, sortKeys(value[key])]),
  )
}

const webPackage = JSON.parse(readFileSync(WEB_PACKAGE_JSON, 'utf8'))
const declared = webPackage.dependencies?.[PACKAGE]
if (!declared) {
  throw new Error(`${PACKAGE} is not a dependency of web/package.json — nothing to freeze`)
}
const resolved = require(`${PACKAGE}/package.json`).version

/**
 * Serialize with each layer on its own line — see the header. Hand-rolled because
 * `JSON.stringify`'s `space` argument is all-or-nothing: it indents every nested
 * expression too, which is where the 30,000 lines come from.
 */
function serialize(document) {
  const head = Object.entries(document)
    .filter(([key]) => key !== 'flavors')
    .map(([key, value]) => `  ${JSON.stringify(key)}: ${JSON.stringify(value)}`)
  const flavors = Object.entries(document.flavors).map(
    ([flavor, layerList]) =>
      `    ${JSON.stringify(flavor)}: [\n` +
      layerList.map((layer) => `      ${JSON.stringify(layer)}`).join(',\n') +
      '\n    ]',
  )
  return `{\n${head.join(',\n')},\n  "flavors": {\n${flavors.join(',\n')}\n  }\n}\n`
}

const frozen = {
  // Stated in the artifact itself, because the reader of a 130 KB JSON blob is not
  // reading this script first.
  _generated: `by scripts/generate-basemap-style.mjs from ${PACKAGE} — DO NOT EDIT; fix the generator`,
  package: {
    name: PACKAGE,
    // The range `web/package.json` declares. This is what the drift test compares,
    // rather than `resolved`, so the test does not need node_modules to run.
    declared,
    // What was actually installed when this file was written. `declared` is a range;
    // two checkouts can satisfy it with different builds, and the layers came from
    // this one.
    resolved,
    license: 'BSD-3-Clause',
  },
  source_id: SOURCE_ID,
  lang: LANG,
  flavors: Object.fromEntries(
    FLAVORS.map((flavor) => [
      flavor,
      sortKeys(layers(SOURCE_ID, namedFlavor(flavor), { lang: LANG })),
    ]),
  ),
}

mkdirSync(path.dirname(OUTPUT), { recursive: true })
writeFileSync(OUTPUT, serialize(frozen))

const counts = FLAVORS.map((flavor) => `${flavor}=${frozen.flavors[flavor].length}`).join(' ')
console.log(`wrote ${path.relative(REPO_ROOT, OUTPUT)}  ${PACKAGE}@${resolved}  layers: ${counts}`)
