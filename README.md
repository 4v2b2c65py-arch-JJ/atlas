# ATLAS

Local-first media discovery & resonance-video platform. Natural-language
and visual queries reach the open web (yt-dlp / YouTube search), local
Ollama models expand and route queries, peer hubs share outlets over a
UDP beacon mesh, and everything lands in a Threads-style Vue feed with
working likes, comments, DMs, and receiver/processor status panels.

## Portal

**Live:** https://web-five-blond-98.vercel.app

Point it at your hub with `?api=` — comma-separate for overlay layers
(first = primary for writes, all = read overlay, feeds merge + dedupe,
SSE connects to every layer, videos stream per-layer):

```
https://web-five-blond-98.vercel.app/?api=http://192.168.100.9:8917
https://web-five-blond-98.vercel.app/?api=http://hub1:8917,http://hub2:8917
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
- **BitTorrent plugin** — `bittorrent.py` + `bt/` (webtorrent node
  runtime): wishlist magnets are resolved locally — largest video file
  downloaded via DHT+trackers, stored through the identity pipeline,
  and published back to the mesh as a LAN `/api/stream` URL.
- **Community resolution** — magnets/peer links go on `/api/wishlist`;
  any hub with a resolver publishes the fetchable URL via
  `POST /api/resolve`, and `community_sync` gossips resolutions across
  the mesh every 2 min into `catalogue.d/` + the peer pool.
- **Manga/manwha/anime** — `manga.py` aggregates MangaDex, Asura Scans,
  Madara/WordPress scan sites (`MADARA_SITES`, e.g. MangaForFree), and
  AniWatch (`aniwatch.py` — catalog search, episode streams resolved
  from MegaPlay embeds via AES-CBC `enc` decrypt + disguised-HLS
  segment download). `/api/manga/chapters` lists chapters,
  `/api/manga/watch` and `/api/aniwatch/watch` replicate content into
  mp4 outlets through the identity pipeline (tall strip pages are
  sliced into portrait segments). Ingest classifier also recognises
  image/comic formats.
- **Social connectors** — `social.py` + ingest `social` kind:
  TikTok/Instagram/Facebook posts fetch through yt-dlp,
  Threads via og-tag scrape; all land in the store pipeline.
  Publishing back out uses Meta Graph API (threads/IG/FB) and TikTok
  Content Posting API — credentials via `POST /api/social/token`
  (stored in `data/social_tokens.json`, never committed).
- **Public tunnel** — `tunnel.py` runs a Cloudflare tunnel exposing
  the hub publicly; `/api/tunnel` manages it. Quick mode
  (`*.trycloudflare.com`, no account) is the fallback; named mode
  creates a stable hostname entirely via the Cloudflare API —
  `POST /api/tunnel {"action":"configure", api_token, account_id,
  zone_id, hostname}` then `{"action":"named"}` provisions the tunnel,
  CNAME, and ingress, stores the connector token in
  `data/cloudflare.json`, and runs `cloudflared tunnel run --token`.
  The URL is fed to `social.set_public_base` automatically.
- **Bot session** — `bot.py` autonomous node: comments on outlets and
  telepathic self/peer DMs over the comms link (toggle in identity.json).
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
| `/api/manga?q=&source=` | GET | manga/manwha search — MangaDex + Asura + Madara sites merged |
| `/api/manga/chapters?id=&source=` | GET | chapter list for a title |
| `/api/manga/watch` | POST | `{manga_id, source?, pages?, chapter?}` chapter → video outlet |
| `/api/aniwatch?q=` | GET | anime catalog search (also merged into `/api/manga`) |
| `/api/aniwatch/episodes?id=` | GET | episode list for an AniWatch title |
| `/api/aniwatch/watch` | POST | `{query?|anilist_id, episode?, seconds?}` episode → video outlet |
| `/api/social` | GET | platform connection status |
| `/api/social/token` | POST | `{platform, access_token, user_id/ig_user_id/page_id, public_base?}` |
| `/api/social/fetch` | POST | `{url}` pull a social post into the store |
| `/api/social/post` | POST | `{platform, src|path, caption?}` publish a stored outlet |
| `/api/tunnel` | GET/POST | tunnel status / `{action: configure\|named\|start\|stop\|restart}` |
| `/api/ingest` | POST | `{uri/magnet/m3u8/mpd/url, title, tags}` |
| `/api/discover` | POST | `{topic, n}` LLM query expansion → candidates |
| `/api/auth/signup\|login\|logout` | POST | API key lifecycle |
| `/api/auth/status` | GET | key/user inventory |
| `/api/catalogue` | GET | builtin + ingested outlets by type |
| `/api/wishlist` | GET | unresolved community descriptors (magnet/peer/hls) |
| `/api/resolved` | GET | community resolutions published by peers |
| `/api/resolve` | POST | `{key/magnet, url}` publish a resolution → shared pool |
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
bittorrent.py    magnet resolver plugin (webtorrent runtime)
bt/              node resolver: bt_download.mjs (npm i webtorrent)
atlas_mcp.py     local MCP tool server
catalogue.d/     ingested outlets organised by transport type
videos/store/    sorted media store + manifest.jsonl
data/            sessions, engagement, comms, peers, api keys
```
