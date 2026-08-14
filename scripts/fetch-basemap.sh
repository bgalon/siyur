#!/usr/bin/env bash
#
# Fetch the dev basemap: a PMTiles extract plus the glyphs and sprites the style
# needs. Regenerates `web/dev-assets/tiles/` and `web/dev-assets/basemap/`, which are
# **gitignored build inputs, not sources** — fix this script, never hand-edit them
# (AGENTS.md, "generated files").
#
#   scripts/fetch-basemap.sh                      # the Rhodes demo window
#   scripts/fetch-basemap.sh --bbox=W,S,E,N       # any area — nothing is per-place
#   scripts/fetch-basemap.sh --bbox=… --name=kyoto
#
# Why a script and not a committed fixture: the extract is ~1.3 MB and the glyph
# ranges ~1.5 MB, all of it machine-produced from a dated upstream build. Committing
# it would put binary churn in every clone to save one command, and the daily build
# it comes from is retained for about a week anyway.
#
# This is the DEV half of `docs/data/tile-source.md`. DU-05 moves the same extract
# into the compiled bundle behind `TileSourceV1`; the client code does not change.
#
# Licences (all in the bundleable set, all already credited by the map's attribution
# control): tile DATA © OpenStreetMap contributors, ODbL-1.0 · Protomaps build
# pipeline and styles BSD-3-Clause · Noto glyphs OFL-1.1 · sprites MIT.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Rhodes old town — the demo default, the same one the rest of the repo uses. It is
# a default, not a hardcoded place: pass --bbox for anywhere else.
BBOX="28.20,36.42,28.25,36.46"
NAME="rhodes"
MAXZOOM=15
ASSETS="https://protomaps.github.io/basemaps-assets"

# Only the ranges the Protomaps style actually reaches for on a Latin/Greek map.
# U+0000–U+04FF: Latin, Latin Extended, IPA, Greek, Cyrillic. A Hebrew, Arabic or
# CJK label has NO glyphs in this set and renders as nothing — see the note in
# `web/src/map/basemap.ts`. Widen here when the demo area needs another script.
RANGES=(0-255 256-511 512-767 768-1023 1024-1279)
FONTSTACKS=("Noto Sans Regular" "Noto Sans Medium" "Noto Sans Italic")

for arg in "$@"; do
  case "$arg" in
    --bbox=*) BBOX="${arg#*=}" ;;
    --name=*) NAME="${arg#*=}" ;;
    --maxzoom=*) MAXZOOM="${arg#*=}" ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

command -v pmtiles >/dev/null || {
  echo "error: the 'pmtiles' CLI is not installed — brew install pmtiles" >&2
  exit 1
}

TILES_DIR="web/dev-assets/tiles"
BASEMAP_DIR="web/dev-assets/basemap"
mkdir -p "$TILES_DIR" "$BASEMAP_DIR/glyphs" "$BASEMAP_DIR/sprites"

# Resolve the daily build at run time and never hotlink it from a client: Protomaps
# warns the URLs may change, and builds are retained only about a week. Walk back
# from today until one answers, so this keeps working on a stale checkout.
build_url=""
for back in 0 1 2 3 4 5 6; do
  day="$(date -u -v-"${back}"d +%Y%m%d 2>/dev/null || date -u -d "-${back} day" +%Y%m%d)"
  candidate="https://build.protomaps.com/${day}.pmtiles"
  if curl -sfI --max-time 20 "$candidate" >/dev/null 2>&1; then
    build_url="$candidate"
    echo "daily build: $day"
    break
  fi
done
[[ -n "$build_url" ]] || { echo "error: no Protomaps daily build answered in the last 7 days" >&2; exit 1; }

echo "extracting $NAME  bbox=$BBOX  z0-$MAXZOOM"
# Keep the whole z0→maxzoom sub-pyramid: partial-zoom extracts are inefficient and
# the low zooms cost almost nothing (stack reference §1).
pmtiles extract "$build_url" "$TILES_DIR/$NAME.pmtiles" --bbox="$BBOX" --maxzoom="$MAXZOOM"

echo "vendoring glyphs"
for stack in "${FONTSTACKS[@]}"; do
  enc="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$stack")"
  mkdir -p "$BASEMAP_DIR/glyphs/$stack"
  for range in "${RANGES[@]}"; do
    curl -sfS --max-time 30 -o "$BASEMAP_DIR/glyphs/$stack/$range.pbf" \
      "$ASSETS/fonts/$enc/$range.pbf"
  done
done

echo "vendoring sprites"
for flavor in light dark; do
  for suffix in .json .png @2x.json @2x.png; do
    curl -sfS --max-time 30 -o "$BASEMAP_DIR/sprites/${flavor}${suffix}" \
      "$ASSETS/sprites/v4/${flavor}${suffix}"
  done
done

# The licence texts that cover what we just vendored, copied from the committed
# originals rather than re-fetched — `compiler/tiles.py` emits these same two files
# into every bundle, so dev and bundle layouts stay identical byte for byte. OFL §2
# requires the licence to accompany the fonts wherever they are redistributed; these
# dev assets are not redistributed, so this is layout parity, not the obligation
# being discharged here. Provenance: DATA-LICENSES.md.
echo "copying licence texts"
cp data/licenses/glyphs/OFL.txt "$BASEMAP_DIR/glyphs/OFL.txt"
cp data/licenses/sprites/LICENSE.md "$BASEMAP_DIR/sprites/LICENSE.md"

printf '\n%s\n' "done — $(du -sh "$TILES_DIR" "$BASEMAP_DIR" | awk '{print $1" "$2}' | tr '\n' ' ')"
echo "tile data © OpenStreetMap contributors (ODbL-1.0) · glyphs OFL-1.1 · sprites MIT"
