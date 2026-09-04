---
name: universal-sdr
description: >
  Universal Software Defined Radio (SDR) & Radio Frequency Monitoring Skill via Model Context Protocol (MCP).
  Enables AI coding agents (Codex, Kimi, Claude, AntiGravity) to query SDR status, inspect real-time RF spectrum
  FFT bins, tune frequencies, switch demodulation modes (AM/USB/LSB/CW/FM), capture audio streams, perform
  multimodal radio station identification, and synchronize results back to the SDR++ UI console.
  Supports live hardware (SDR++ + RTL-SDR) and zero-hardware simulation (Mock Backend).
  Activate whenever the user requests radio tuning, shortwave (HF) monitoring, spectrum analysis,
  broadcast identification, or RTL-SDR control.
license: MIT
---

# Universal SDR Agent Skill (Model Context Protocol)

This skill enables AI agents (**Codex**, **Kimi**, **Claude**, **AntiGravity**, etc.) to control Software Defined Radio (SDR) receivers, analyze RF spectrum data, capture live demodulated radio broadcasts, and deduce station identification through the standardized **Universal SDR MCP Server**.

---

## 🧭 1. Where to Get & Run the MCP Server

This repository (`universal-sdr-mcp`) is the **official, self-contained MCP Server and Skill package**.

### Quick Setup (1 Command)
```bash
# In the root of this repository:
pip install fastmcp
```

### Running the Server
```bash
# Real hardware mode (with SDR++ and RTL-SDR running):
python3 -m sdr_mcp --backend sdrpp

# Offline / Simulation mode (zero hardware needed):
python3 -m sdr_mcp --backend mock
```

If you are cloning this into an external project:
```bash
git clone https://github.com/ygud/universal-sdr-mcp.git
cd universal-sdr-mcp
pip install fastmcp
```

---

## ⚙️ 2. MCP Client Configuration

Configure the MCP client in your agent configuration file (e.g. `claude_desktop_config.json`, `codex_config.json`, or agent environment):

### Standard stdio Configuration

```json
{
  "mcpServers": {
    "universal-sdr": {
      "command": "python3",
      "args": [
        "-m",
        "sdr_mcp",
        "--backend",
        "sdrpp"
      ],
      "cwd": "/path/to/universal-sdr-mcp"
    }
  }
}
```

### Selecting Backend Mode
- `--backend sdrpp` (**Default / Production**): Connects to live SDR++ instance (`127.0.0.1:19870`). RTL-SDR dongle must be owned and running in SDR++.
- `--backend mock` (**Simulation / Offline / CI**): Zero hardware required. Generates synthetic RF spectrum and 440 Hz test audio for development or daytime testing when HF propagation is offline.

---

## 📋 3. Standard Operating Procedure (SOP)

When assigned a radio monitoring or station hunting task, follow this deterministic 6-step loop:

```
[1. Status Check] ──> [2. Spectrum Scan] ──> [3. Tune & Lock]
                                                     │
[6. Sync UI] <── [5. Multimodal Identify] <── [4. Sample Audio]
```

### Step 1: Health & Connection Verification
Always verify backend availability before executing RF actions:
- Call `sdr_status()`.
- Inspect `connected`:
  - If `false`, notify the user that SDR++ is not reachable on `127.0.0.1:19870`, or switch to mock mode via `sdr_switch_backend("mock")`.
  - If `true`, verify current frequency and modulation mode.

### Step 2: Spectrum Power Inspection
Inspect the passband FFT to detect active carriers:
- Call `sdr_get_spectrum(bin_count=256)`.
- Inspect `peak_db` vs `avg_db`:
  - If `peak_db - avg_db > 10.0 dB`, a strong radio transmission is present at `peak_frequency`.
  - Note the frequency offset from the current center.

### Step 3: Precise Tuning
Lock the demodulator onto the carrier:
- Call `sdr_tune(frequency=TARGET_HZ, mode=MODE)`.
  - Common HF broadcast bands: Shortwave (5.9 – 18 MHz), AM mode.
  - Amateur radio: 7.050 MHz (LSB), 14.200 MHz (USB).
  - FM broadcast: 88.0 – 108.0 MHz, WFM mode.

### Step 4: Audio Sampling & SNR Verification
Capture a short demodulated segment to verify signal presence:
- Call `sdr_get_audio(duration_sec=5.0)`.
- Inspect return values:
  - `path`: Location of the recorded WAV file (typically `/tmp/sdr_sample.wav`).
  - `rms`: Root-Mean-Square audio amplitude. If `rms < 10.0`, the channel is dead silence or weak background hiss. If `rms > 50.0`, speech/music is clearly audible.
  - `peak`: Peak sample value.

### Step 5: Auditory Deduction (Multimodal Station ID)
Inspect the audio file directly (using multimodal audio models or audio inspection tools) to extract clues:
- **Spoken Language**: Mandarin, Korean, Japanese, English, Russian, Arabic, Spanish, etc.
- **Acoustic Signatures**: Time pips (hourly chimes), interval signal tunes (music box melodies), fanfare, distinctive jingles.
- **Content Type**: News broadcast, commentary, revolutionary music, commercial advertisements, amateur callsign exchange.
- **Reference HF Schedules**: Correlate frequency with known international HF schedules (AOKI / HFCC / EiBi).

### Step 6: Closed-Loop UI Feedback
Push the deduction back to the user's SDR++ screen so the operator can view the AI results:
- Call `sdr_update_analysis(country=..., language=..., station=..., program=..., confidence=..., evidence=[...], dialect=...)`.
- The SDR++ ImGui Agent Console will immediately update its display with your analysis.

---

## 🛠️ 4. MCP Tool Reference

| Tool Name | Arguments | Description | Key Returns |
|---|---|---|---|
| `sdr_status` | None | Queries receiver state, connection, and audio stream status. | `frequency`, `mode`, `connected`, `audio_ready`, `backend` |
| `sdr_devices` | None | Lists available radio hardware sources. | `available_sources`, `active_device` |
| `sdr_tune` | `frequency: float`, `mode: str = None` | Tunes SDR to center frequency in Hz and optional mode (`AM`, `WFM`, `NFM`, `USB`, `LSB`, `CW`). | `success`, `frequency`, `mode` |
| `sdr_get_spectrum` | `bin_count: int = 256` | Returns real-time RF power spectrum FFT in dB. | `available`, `bins`, `peak_db`, `peak_frequency`, `avg_db` |
| `sdr_get_audio` | `duration_sec: float = 5.0`, `frequency: float = None`, `mode: str = None` | Samples demodulated audio to a WAV file with RMS and peak metrics. | `success`, `path`, `sample_rate`, `rms`, `peak` |
| `sdr_start_recording` | `path: str = None` | Starts continuous audio recording to WAV. | `status: "started"`, `path` |
| `sdr_stop_recording` | None | Stops continuous recording and finalizes WAV file. | `status: "stopped"`, `duration_sec`, `size_bytes` |
| `sdr_switch_backend` | `backend_type: str` | Switches backend at runtime (`"sdrpp"` or `"mock"`). | `status: "ok"`, `active_backend` |
| `sdr_update_analysis` | `country`, `language`, `station`, `program`, `confidence`, `evidence`, `dialect` | Pushes station identification results to SDR++ UI console. | `success: true` |
| `sdr_set_gain` | `gain_db: float` | Reports `NOT_SUPPORTED` for SDR++ backend. | `supported: false`, `status: "NOT_SUPPORTED"` |

---

## 🚫 5. Hard Boundaries & Safety Rules

1. **Strict Hardware Ownership Integrity**:
   - SDR++ is the sole owner of the RTL-SDR dongle.
   - NEVER attempt to access the RTL-SDR hardware directly using `pyrtlsdr`, `rtl_tcp`, or shell commands. It causes device bus lockouts.
2. **Zero-Guessing / Strict Data Honesty**:
   - If audio RMS indicates noise (`rms < 20.0`) or unintelligible speech, assign appropriate low confidence (`< 0.3`). Never fabricate a station name.
   - If running `--backend mock`, always inform the user that output is simulated data.
3. **No GUI Clicking Bypass**:
   - Do not use mouse clicking or screen scraping to control SDR++. All interactions must flow through the MCP server.
