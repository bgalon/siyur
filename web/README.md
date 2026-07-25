# web/ — Siyur PWA (Vite + MapLibre + PMTiles + OPFS)

Placeholder marker. The Vite/PWA app is scaffolded at **DU-00 unit f** from the
ADR-0003 config spike (`spike/vite_spike/`, proven 2026-07-25): `vite@8.1.5` +
`vite-plugin-pwa@1.3.0` + `workbox@7.4.1`, `worker.format:'es'`, the PMTiles archive
runtime-fetched into OPFS (never imported / never in `public/`).

`web/` is a JS/Vite application, **not** a Python package — it is intentionally
excluded from `pyproject.toml`'s `[tool.hatch.build.targets.wheel] packages`.

DU-00 target: an empty MapLibre map renders (base style over HTTP, ADR-0002
online-first — OPFS is a later transport swap) and Google SSO login works (Firebase
Auth emulator locally).
