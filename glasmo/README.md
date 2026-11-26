# GLASMO

**Global Live Adaptive Signal Modulation Organism**

An organic signal processing ecosystem that captures real hardware entropy (quantum RNG, QPC timing, process table, thermal, network) and transforms it into cinematic, catalogueable, streamable experiences — with zero simulation.

## Public API

The public interface is defined in [`api/openapi.yaml`](api/openapi.yaml). Services expose:

| Service | Endpoint | Description |
|---------|----------|-------------|
| Organic IPTV | `http://localhost:3032` | Catalogue conversion, live signal WebSocket |
| Prism Avatar | `http://localhost:3033` | 12-camera cinematic stream, polygon aviation/elongation |

### Quick Connect

```bash
# Live organic signal stream (WebSocket)
ws://localhost:3032/signals/organic

# Universal catalogue (BC/DC/AC/NOW epochs)
curl "http://localhost:3032/catalogue/universal?epoch=NOW&format=json"

# Prism avatar cinematic frame stream
ws://localhost:3033/avatar/prism

# Current polygon state (12 solids aviate + elongate)
curl http://localhost:3033/avatar/polygons
```

## Documentation

- **GitBook**: [`docs/gitbook/`](docs/gitbook/) — User guide & architecture
- **GitNote**: [`docs/gitnote/`](docs/gitnote/) — Operational runbooks
- **GitSpotlight**: [`docs/gitspotlight/`](docs/gitspotlight/) — Feature highlights

## Repository Structure

```
GLASMO/
├── api/
│   └── openapi.yaml          # Public API contract (OpenAPI 3.0)
├── docs/
│   ├── gitbook/              # User documentation
│   ├── gitnote/              # Operational notes
│   └── gitspotlight/         # Feature highlights
├── .gitmodules               # Private implementation submodules
├── README.md
└── LICENSE
```

## Private Implementation (Submodules)

The actual signal engines are kept in private submodules:

- `signal-engine/` — BCryptGenRandom, QPC, Get-Process, WMI thermal, network adapters
- `taxonomy-engine/` — 6→18→42 organic classification
- `cataloguer/` — Universal BC/DC/AC/NOW cataloguer with celestial calendar
- `iptv-gateway/` — Organic IPTV server (M3U/XSPF/PLS/Organic + WebSocket)
- `prism-avatar/` — 12-camera cinematic recorder, 12 aviatng/elongating polygons
- `alien-tag/` — GNUME=GNOME=GANU_ \| GANDAL DANSLAIF DANFALL EDSON NEWSOON

## Key Features

- **Zero simulation** — All signals from real hardware
- **No microphone** — winmm.dll AccessViolationException avoided
- **12 Polygons** — Platonic (5), Archimedean (5), Kepler-Poinsot (2) that aviate & elongate
- **Rognaic entropy** — Domain shell shock rate injected into M3U profiles
- **ADC/MCP/Clock** — Hardware sensors, local AI models, NTP sync in timeline
- **Alien tag** — Embedded in every catalogue entry and stream

## Alien Interpreter Tag

```
GNUME=GNOME=GANU_ | GANDAL DANSLAIF DANFALL EDSON NEWSOON
```

## License

MIT — See [LICENSE](LICENSE)