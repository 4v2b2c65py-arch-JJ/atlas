# GLASMO GitNote - Operational Notes

## Signal Sources (Verified Real Hardware)
- **Quantum RNG**: BCryptGenRandom (CNG AES-256 CTR_DRBG) - 256-bit entropy
- **QPC Timing**: QueryPerformanceCounter - nanosecond resolution delta
- **Process Table**: Get-Process - live PIDs, CPU, memory, handles
- **Thermal**: WMI MSAcpi_ThermalZoneTemperature - CPU/GPU/ambient zones
- **Network**: Get-NetAdapterStatistics - bytes sent/received per adapter

## Taxonomy Map (6→18→42)
Sources: quantum, timing, process, thermal, network, calendar
Categories: entropy, rhythm, load, temperature, traffic, celestial
Branches: 42 unique signal-to-meaning mappings

## Epoch Definitions
- **BC** (Before Computing): Pre-1940s analog signals
- **DC** (During Computing): 1940s-2020s digital transition
- **AC** (After Computing): 2020s+ post-digital organic
- **NOW**: Live real-time signal capture

## Cinematic Recording (Prism Avatar)
- 12 simultaneous camera angles
- Tape roll recorder with frame sequences
- Organic log per frame (entropy + taxonomy + celestial)
- MANIFEST.json per recording session

## M3U Profile Generation
- Custom directive parser (no external libs)
- Rognaic shell shock rate from domain entropy
- Alien tag header injection
- Profile tags: quantum, timing, process, thermal, network, calendar

## ADC/MCP/Clock Integration
- ADC: Thermal zones, voltage rails, fan RPM, battery %
- MCP: Ollama/LM Studio local model health + loaded models
- Clock: NTP sync (stratum, offset, reference ID)

## Deployment Notes
- No microphone (winmm.dll AccessViolationException)
- NTP requires UDP 123 outbound
- Ollama/LM Studio must run locally for MCP
- WebSocket servers on :3032 (IPTV) and :3033 (Avatar)