# ATLAS

Local-first media discovery & resonance-video platform. Natural-language
and visual queries reach the open web (yt-dlp / YouTube search), local
Ollama models expand and route queries, peer hubs share outlets over a
UDP beacon mesh, and everything lands in a Threads-style Vue feed with
working likes, comments, DMs, and receiver/processor status panels.

## Portal

Static frontend deploys to Vercel — point it at your hub with `?api=`:

```
https://<your-app>.vercel.app/?api=http://<hub-ip>:8917
```

The key is persisted in the browser; all API calls then round-trip to
your hub (CORS is open, SSE live updates included).

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install mcp yt-dlp opencv-python numpy
.venv/bin/python server.py          # hub on :8917 (LAN reachable)
# open http://localhost:8917
```

Optional: `ollama pull llama3.2` enables LLM query expansion/classification.

## Features

- **Smart search** — literal token match first (instant), LLM expansion on
  miss, real `ytsearch` web reach, catalogue + peer-pool fallback with
  uptime probes. No more false "no outlets" dead-ends.
- **Auto-fetch pipeline** — yt-dlp → inbox → identity classify → sorted
  store; sub-gate media is upscaled (Lanczos) instead of dropped.
- **Stream tuner** — background query rotation keeps the feed fresh.
- **Spectator mode** — drop an image for dHash visual retracing, or drop
  text as a query.
- **Peer mesh** — UDP beacon :8918 auto-discovers hubs; peer URL pool and
  query relay share discoveries between nodes.
- **Comms** — likes/comments/reposts/DMs persisted server-side, SSE push.
- **API keys** — HuggingFace-style `atlas_…` tokens: signup/login/logout,
  usage tracking, persisted recovery (`data/api_keys.json`), optional
  `api.auth_required` enforcement via `X-ATLAS-Key`.
- **Stream ingest** — `POST /api/ingest` accepts `url`, `uri`, `magnet:`,
  `.m3u8`, `.mpd`, peer URIs; auto-classified into `catalogue.d/<type>/`.
- **LLM discovery** — `POST /api/discover {"topic": "..."}` expands a topic
  into diverse queries and merges web candidates.
- **MCP server** — `atlas` stdio tools: probe, storage, resonance render,
  auto-zoom, fidelity enhance, upscale, classify, store, models.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/feed?q=&cat=` | GET | outlet cards (LLM-expanded on miss) |
| `/api/search?q=&fetch=` | GET | smart search + auto-fetch; raw URL fetches |
| `/api/vision` | POST | `{image: b64}` reverse-image search |
| `/api/fetch` | POST | `{url, title?}` fetch any outlet |
| `/api/ingest` | POST | `{uri/magnet/m3u8/mpd/url, title, tags}` |
| `/api/discover` | POST | `{topic, n}` LLM query expansion → candidates |
| `/api/auth/signup\|login\|logout` | POST | API key lifecycle |
| `/api/auth/status` | GET | key/user inventory |
| `/api/catalogue` | GET | builtin + ingested outlets by type |
| `/api/uptime` | POST | `{urls[]}` reachability probe |
| `/api/peers` | GET/POST | peer URL pool / hub registry |
| `/api/events` | GET | SSE: engage, comms, fetched, peers |
| `/api/engage` | GET/POST | likes/comments/reposts |
| `/api/comms?node=` | GET/POST | node DMs |
| `/api/node` `/api/ip` | GET | receiver/processor/radio status |

## Deploy (Vercel)

```bash
npm i -g vercel
vercel link            # or: vercel --yes
vercel --prod          # serves web/ as a static portal
```

Then open the printed URL with `?api=http://<hub>:8917`.

## Repo layout

```
server.py        hub backend (stdlib HTTP + SSE + beacon)
web/index.html   Vue 3 Threads-style frontend (static, Vercel-ready)
identity.py      observer profile, classifier, quality gate, store router
llm.py           Ollama client: classify refine, query expand, suggest
vision.py        dHash visual search + optional vision-model hooks
beacon.py        radio detect + UDP peer beacon
catalogue.py     verified builtin outlets
ingest.py        stream descriptor intake -> catalogue.d/<type>/
api_keys.py      atlas_ token service (signup/login/track/recover)
upscale.py       Lanczos upscale for sub-gate media
atlas_mcp.py     local MCP tool server
catalogue.d/     ingested outlets organised by transport type
videos/store/    sorted media store + manifest.jsonl
data/            sessions, engagement, comms, peers, api keys
```
