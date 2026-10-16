# Deployment — everything on Vercel (2026-09-21)

```
Browser ──HTTPS──► Vercel project (one deployment, one origin)
                    ├── React/Vite build          → Vercel CDN   (/, /dashboard, /assets/*)
                    └── FastAPI (one Python Function, region bom1 = Mumbai)   (/api/*, /health, /docs)
                              │  outbound traffic through the project's Static IPs (bom1)
                              ▼
                        api.imd.gov.in   X-API-Key: key registered for the Vercel static egress IP
                                         Authorization: Bearer <token the backend generates itself>
```

The frontend calls the API on the **same origin** (`/api/...`). `VITE_API_BASE_URL` must stay **unset** on Vercel.

## 1. What deploys

| File | Role |
|---|---|
| `pyproject.toml` (repo root) | Vercel's Python manifest: **runtime dependencies only**, `[tool.vercel] entrypoint = "app:app"`, frontend served from the CDN (`[tool.vercel.fastapi.static] cdn = true`) |
| `uv.lock` (repo root) | Pinned versions for that manifest |
| `app.py` (repo root) | Entrypoint: puts `backend/` on the import path and exposes `floodnet.api.main:app` |
| `vercel.json` | `framework: fastapi`; build command `cd frontend-react && npm ci && VITE_BASE=/ npm run build`; `regions: ["bom1"]`; function `maxDuration: 300` (Hobby maximum); `excludeFiles` for tests, raw data, docs, `.env` |
| `.vercelignore` | Never uploaded: `.env*`, `backend/.venv`, `backend/tests`, `node_modules`, `data/raw`, `data/interim`, `docs`, `research` |
| `backend/floodnet/api/main.py` | On Vercel (`VERCEL=1`), registers `app.frontend("/", directory="frontend-react/dist", fallback="index.html")`. Every API route takes priority over the frontend, and navigation requests such as `/dashboard` get `index.html`. The local-server `/` and `/static` routes are not registered there |

Local development is unchanged: `cd backend && .venv/Scripts/python -m uvicorn floodnet.api.main:app --port 8000`
serves the build at `/static`, and `npm run dev` proxies `/api` to it. Test tools live in `backend/pyproject.toml`'s
`dev` extra (`pip install -e "./backend[dev,radar]"`).

## 2. Requirements on the Vercel side

- **Plan: Pro or Enterprise.** Static IPs are not available on Hobby. They cost $100/month per project plus Private
  Data Transfer (Vercel docs, checked 2026-09-21).
- **No Large Functions.** Static IPs do not support the Large Functions beta, the extended max duration beta
  (above 800 s), or container images. FloodNet uses none of them (§6, §7).

## 3. Static IP → IMD API key

1. Vercel dashboard → the project → **Settings → Networking** (the docs call the product "Static IPs" under
   connectivity) → **Static IPs** → **Manage Active Regions** → choose **bom1 (Mumbai)**. It must be the same region
   as `vercel.json` `regions`, because Static IPs are per region.
2. Copy the static IP addresses shown for bom1. Vercel assigns a static IP **pair** per region.
3. Verify from the running function (optional, needs `FLOODNET_ADMIN_TOKEN`, see §4):
   `curl -H "X-Admin-Token: $FLOODNET_ADMIN_TOKEN" https://<app>.vercel.app/api/admin/egress-ip`
   → `{"backend_public_egress_ip": "…", "consistent": true, "vercel_region": "bom1", …}`. It must be one of the
   dashboard IPs. Call it a few times: traffic may leave through either IP of the pair.
4. On the IMD portal, generate the production `IMD_API_KEY` for that IP. Never use a developer laptop's IP or a
   visitor's IP.

**Open risk [UNVERIFIED]:** IMD binds a key to one IP, and Vercel gives a *pair*. If outbound traffic uses both
addresses, requests from the unregistered one will get HTTP 403 "IP address not authorized". Ask IMD whether one key
can list both IPs, or whether each IP needs its own key. `/api/admin/egress-ip`, called repeatedly, shows which
addresses are actually used.

## 4. Environment variables (Vercel → Settings → Environment Variables)

| Variable | Scope | Notes |
|---|---|---|
| `IMD_EMAIL`, `IMD_PASSWORD` | Production (+ Preview if wanted), **Sensitive** | IMD account; the backend generates and renews the token itself |
| `IMD_API_KEY` | same | the key registered for the static egress IP |
| `FLOODNET_ADMIN_TOKEN` | Production, Sensitive, optional | enables `/api/admin/egress-ip` and `/api/admin/imd-token`; unset = both return 404 |
| `GOOGLE_TTS_API_KEY` | optional | neural voice |

These are read only by the Python function. **No `VITE_` variable**, because Vite embeds `VITE_*` values in the
public bundle. Do **not** set `VITE_API_BASE_URL`, `IMD_API_TOKEN` or `IMD_API_TOKEN_FILE` on Vercel: there is no
token to paste, and the token file is a local-development option. `.env` is never uploaded (`.vercelignore`) and
never bundled (`excludeFiles`).

## 5. Serverless behaviour: what is per instance, and why it is safe

A Vercel Function instance keeps memory while warm, but instances are started, reused and discarded by the
platform. The only writable disk is `/tmp`, which is per instance and not persistent.

| State | Where it lives now | On a cold or different instance |
|---|---|---|
| Simulation results | **in the response itself.** `POST /api/simulate`, `/api/compare` and `/api/storm/replay` return the complete run (`backend/floodnet/api/bundle.py`): every frame's depth image data, street depths, drainage node/edge state, the series, hotspots, the CAP draft and street-inspector context. The browser rebuilds frames locally (`frontend-react/src/lib/runBundle.js`), and route requests carry the frame's depths | nothing to look up: no follow-up request needs the instance that computed the run. The per-run GET endpoints (`/api/simulation/{run_id}/…`) still work on a single local server for debugging, but the UI never calls them |
| IMD token | process memory (`imd_auth.manager`) | Generated automatically on that instance's first IMD request (one token request per new instance) |
| Background token keeper | **off on Vercel** (a frozen instance cannot run it) | renewal happens inside requests: no token, under 10 min left, or a 401 |
| Last good rainfall field, decoded radar frames | `/tmp/floodnet` (`config.STATE_DIR`) | warm-instance cache only; a cold instance simply has no cached field yet |
| Upstream response caches (IMD, ECMWF, radar) | process memory, TTL | refetched |
| `/api/data-status` "active source" | the last run on **that** instance | may read "no run yet" on a fresh instance |

No database is added. Measured response sizes: 0.84 MB for 60 min, about 1.3 MB for the 180-min UI default, and
about 2 MB for 360 min, all under Vercel's 4.5 MB response limit. A result is not kept after the page is closed or
reloaded; if run links ever need to survive, the next step would be durable storage (Vercel Blob), not instance memory.
On Vercel the longest horizon accepted is 360 min (`FLOODNET_MAX_HORIZON_MIN`), so a run finishes inside the
300 s limit; longer requests get a clear 422, and a platform timeout (504) is shown as a time-limit message.

## 6. Function bundle size (measured)

Linux x86_64 wheels for Python 3.12 from `uv.lock`, uncompressed, measured 2026-09-21 (Vercel does not tree-shake Python):

| Part | Size |
|---|---|
| Dependencies, all files | 239.6 MiB (251.2 MB) |
| Dependencies without their bundled `tests/` folders (excluded in `vercel.json`) | 216.2 MiB (226.7 MB) |
| FloodNet code + pilot data (without `swmm/`) + React build | 3.6 MiB |
| **Function total with the exclusions** | **219.9 MiB (230.5 MB)** |
| Function total if `excludeFiles` does not reach installed packages | 243.2 MiB (255.0 MB) |

Largest dependencies: scipy 62.1 MiB + scipy.libs 29.8, numpy.libs 26.2 + numpy 20.8, pyproj 20.7 + pyproj.libs 12.7,
pillow.libs 13.4 + PIL 5.2, shapely.libs 5.6 + shapely 4.1, networkx 5.0, pydantic_core 4.7. All of them are imported
by the running API (checked by loading the app and running a simulation). None can be removed without breaking the
simulation, routing, the terrain transforms or the radar-derived source.

Vercel's limit for **Python** functions is **500 MB** uncompressed, and Static IPs work at that size. The general
250 MB limit applies to other runtimes. FloodNet is under 250 MB with the exclusions. Vercel also adds precompiled
`.pyc` files "when space allows". **The exact size Vercel reports is only known after the first real build**; record it in §9.

## 7. Execution time and memory (measured locally, NOT on Vercel)

Sequential requests pinned to **one CPU core, one math-library thread** (to approximate Vercel's default 1 vCPU /
2 GB) on the developer laptop (Intel i5-13450HX), 2026-09-21. A Vercel vCPU is probably slower than this core. Treat
these as lower bounds until they are measured on Vercel (§9, step 9).

| Request | Local time (1 core) | Response | Peak memory (process) |
|---|---|---|---|
| cold import of the app | 2.1 s | – | 76 MiB |
| pilot load (`/api/meta`, first call) | 0.3 s | 4 KiB | 91 MiB |
| small run: cloudburst, 60 min horizon | 19.6 s | 5 KiB | 134 MiB |
| **full current pilot run: cloudburst, 180 min (UI default)** | **66.3 s** | 5 KiB | 153 MiB |
| map frame / series (after a run) | 0.5 s / 0.6 s | 1.3 MiB / 0.5 MiB | 153 MiB |
| route / route alternatives | 0.1 s / 1.5 s | 9 / 17 KiB | 153 MiB |
| historical replay, 26 July 2005 | 127.5 s | 5 KiB | 175 MiB |
| compare (normal vs 30 % blocked) | 62.8 s | 15 KiB | 194 MiB |
| radar-derived (IMD DWR SRI image) run, live download | 47.1 s | 9 KiB | 530 MiB |
| ECMWF forecast run, live download | 40.5 s | 7 KiB | 530 MiB |
| IMD live run | 0.6 s → **503** (HTTP 403 from IMD: this laptop's IP is not the key's IP) | – | – |
| "Best available source" run (fell back to radar-derived) | 46.5 s | 10 KiB | 599 MiB |
| longest allowed: cloudburst, 720 min horizon | **234.6 s** | 6 KiB | 599 MiB |

Peak memory stays under 0.6 GiB, well inside the 2 GB default. The 720-min horizon (235 s on this core) and the
replay (128 s) are the risks against time limits.

`vercel.json` sets **`maxDuration: 300`**, the Hobby maximum. The UI always runs 180 min, which measured about 66 s
locally, and the API refuses horizons above 360 min on Vercel. On Pro this can be raised to 800 s. Every response is far below Vercel's 4.5 MB response limit: the largest, one map frame, is 1.3 MiB.

## 8. IMD authentication (automatic)

The only manual IMD setup is the three Vercel environment variables `IMD_EMAIL`, `IMD_PASSWORD` and `IMD_API_KEY`.
The backend calls `POST https://api.imd.gov.in/api/oauth/token.php` itself when there is no token, when fewer than 10
minutes are left, or after an IMD 401. After a 401 it retries the original request exactly once. Renewal is
single-flight within an instance. A 403 never triggers renewal. A failed generation backs off for 60 s, or 15 min if
IMD refuses the credentials. Details, states and tests: `docs/IMD_TOKEN_RENEWAL.md`.

| Situation | `imd_auth_status` | IMD live | "Best available source" |
|---|---|---|---|
| valid | `VALID` | answers | IMD LIVE |
| under 10 min left | `EXPIRING_SOON` → `RENEWING` | new token first; keeps the current one if that fails | IMD LIVE |
| IMD 401 | `RENEWING` → `VALID`, or `EXPIRED` + refresh `failed` | one new token, one retry | IMD LIVE, or falls back only if renewal failed |
| credentials refused | `UNAVAILABLE` | backs off 15 min | falls back |
| IMD 403 (key / IP) | `UNAVAILABLE` (`rejected_by: key_or_ip`) | no renewal | falls back; fix the key's IP binding (§3) |
| timeout / 5xx | unchanged | that request fails | falls back for that run |

Fallback order: IMD LIVE → IMD DWR RADAR-DERIVED → ECMWF NWP → CACHED → DEMO, each labelled as itself, never as IMD LIVE.

Other hosts (Docker, a VM) remain possible. There the background keeper pre-warms the token, and the token or
credentials may also come from AWS Secrets Manager / SSM (`IMD_CREDENTIALS_PROVIDER`, `IMD_TOKEN_PROVIDER`; see
`.env.example`). The `Dockerfile` still works but is not part of the Vercel deployment.

## 9. Health and production checklist

`GET /health` → `{"ok":true}` (no dependencies). `GET /api/data-status` → `imd_auth_status`, `imd_refresh_status`,
`active_rainfall_source`, `active_source_timestamp` (plus source health). It never contains a password, key or token.

| # | Step | How to check | Result |
|---|---|---|---|
| 1 | Deploy (`vercel deploy` or Git) | build log green; **record the function size Vercel reports** | not done |
| 2 | Frontend | `/` and `/dashboard` load and survive a refresh | not done |
| 3 | API | `/health` → `{"ok":true}`; `/api/data-status` → JSON | not done |
| 4 | Static IPs (bom1) | dashboard shows the pair; `/api/admin/egress-ip` returns one of them | not done |
| 5 | IMD key | generated on the IMD portal for that IP (see the open risk in §3) | not done |
| 6 | Env vars | `IMD_EMAIL`, `IMD_PASSWORD`, `IMD_API_KEY` set as Sensitive; no `VITE_*` | not done |
| 7 | IMD live | choose "IMD live observation" → run completes; badge **IMD LIVE**; `imd_auth.token_source` = "IMD token endpoint (auto-renewed)" | not done |
| 8 | Renewal | redeploy or wait for a new instance → first IMD request generates a token by itself; `imd_refresh_status` = renewed | not done |
| 9 | Timings on Vercel | full cloudburst run and replay complete within `maxDuration`; record them next to §7 | not done |
| 10 | Bundle scan | built JS has no `IMD_EMAIL`, `IMD_PASSWORD`, `IMD_API_KEY`, `access_token`, `Authorization`, JWT, localhost | PASS locally (2026-09-21) |
