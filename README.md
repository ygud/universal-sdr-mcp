# Universal SDR MCP Server

Universal, hardware-agnostic Model Context Protocol (MCP) server for Software Defined Radio (SDR).

Enables any MCP-compliant AI Agent (**Claude Desktop**, **AntiGravity**, **Cursor**, **Codex**, **Gemini**, etc.) to observe, tune, capture spectrum FFT data, and record demodulated audio through a standardized toolset.

```
+-----------------------------------------------------------------------+
|                       MCP Clients / AI Agents                         |
|           (Claude Desktop / AntiGravity / Cursor / Codex)             |
+-----------------------------------------------------------------------+
                                   |
                       MCP Protocol (stdio / SSE)
                                   v
+-----------------------------------------------------------------------+
|                      Universal SDR MCP Server                         |
|                             (sdr_mcp)                                 |
+-----------------------------------------------------------------------+
                                   |
                  Abstract Interface: SDRBackend
                                   |
         +-------------------------+-------------------------+
         |                                                   |
         v                                                   v
+------------------+                               +--------------------+
|  SdrppBackend    |                               |    MockBackend     |
| (127.0.0.1:19870)|                               | (Zero HW, CI/Dev)  |
+------------------+                               +--------------------+
         |
    TCP JSON-RPC
         v
+------------------+
|   SDR++ Plugin   |
|  (sdrpp_agent)   |
+------------------+
         |
+------------------+
|  RTL-SDR Dongle  |
| (Exclusive Owner)|
+------------------+
```

---

## Key Design Principles

1. **Hardware Ownership Integrity**:
   - SDR++ remains the sole and exclusive owner of the physical RTL-SDR dongle.
   - Python / MCP code **never** accesses librtlsdr directly (`pyrtlsdr`, `rtl_tcp`, etc. are forbidden). This avoids USB bus conflicts, device busy errors, and UI freezing.
2. **Strict Data Honesty**:
   - No mock/cached fallbacks hidden in production mode: if real SDR data or audio is unavailable, an explicit error is returned.
   - Unsupported hardware capabilities (e.g. gain control inside SDR++) report explicit `NOT_SUPPORTED` rather than faking success.
3. **Zero Computer Use Requirement**:
   - Everything runs via deterministic JSON-RPC IPC and native MCP tools. No visual screen scraping or mouse automation needed.
4. **Interchangeable Backends**:
   - `sdrpp`: Connects to live SDR++ instance via internal TCP bridge.
   - `mock`: Pure synthetic software simulation generating carriers, Gaussian noise, and 440 Hz test tones for development, CI, or daytime testing.

---

## Installation & Requirements

### Prerequisites
- Python 3.10+
- [FastMCP](https://github.com/jlowin/fastmcp): `pip install fastmcp`
- macOS / Linux

```bash
cd /Users/xanaduxuan/Documents/antigravity/sdr
pip install fastmcp
```

---

## Quickstart

### 1. Run in Mock Mode (No Hardware Needed)
Ideal for testing MCP clients, development, or daytime HF propagation downtime:
```bash
python3 -m sdr_mcp --backend mock
```

### 2. Run with Live SDR++ (Real Hardware)
1. Launch SDR++ with the `sdrpp_agent` plugin loaded.
2. Start the MCP server:
```bash
python3 -m sdr_mcp --backend sdrpp
```

### 3. Run Automated Tests
```bash
python3 -m unittest discover -s sdr_mcp/tests
```
(Runs all 25 unit tests across Mock backend, SDR++ live IPC bridge, and MCP tools).

---

## MCP Client Configuration

### Claude Desktop
Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
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
      "cwd": "/Users/xanaduxuan/Documents/antigravity/sdr"
    }
  }
}
```

### AntiGravity / Custom Agents
```json
{
  "name": "universal-sdr",
  "command": "python3",
  "args": ["-m", "sdr_mcp", "--backend", "sdrpp"],
  "cwd": "/Users/xanaduxuan/Documents/antigravity/sdr"
}
```

---

## MCP Tool Reference

| Tool Name | Arguments | Description | Return Values |
|-----------|-----------|-------------|---------------|
| `sdr_status` | None | Queries current receiver state, tuned frequency, mode, and audio stream status. | `frequency`, `frequency_khz`, `mode`, `audio_ready`, `connected`, `backend` |
| `sdr_devices` | None | Lists available radio hardware sources and input drivers. | `available_sources`, `active_device`, `count` |
| `sdr_tune` | `frequency: float`, `mode: str = None` | Tunes SDR to center frequency (Hz) and optionally sets modulation (`AM`, `WFM`, `NFM`, `USB`, `LSB`, `CW`). | `success`, `frequency`, `mode` |
| `sdr_get_spectrum` | `bin_count: int = 256` | Returns real-time RF power spectrum FFT data (in dB) across passband. | `available`, `bins: List[float]`, `peak_db`, `peak_frequency`, `avg_db` |
| `sdr_get_audio` | `duration_sec: float = 5.0`, `frequency: float = None`, `mode: str = None` | Samples live demodulated audio segment to a WAV file on host. Computes RMS and peak levels. | `success`, `path`, `duration_sec`, `sample_rate`, `samples_recorded`, `rms`, `peak` |
| `sdr_start_recording` | `path: str = None` | Starts continuous background recording to a WAV file. | `status: "started"`, `path`, `sample_rate` |
| `sdr_stop_recording` | None | Stops continuous recording and finalizes WAV headers. | `status: "stopped"`, `path`, `duration_sec`, `samples_recorded`, `size_bytes` |
| `sdr_set_gain` | `gain_db: float` | Sets receiver RF gain in dB (returns `NOT_SUPPORTED` on SDR++). | `supported: False`, `status: "NOT_SUPPORTED"` |
| `sdr_set_sample_rate` | `sample_rate: float` | Configures input frontend sampling rate in Hz. | `success`, `sample_rate` |
| `sdr_switch_backend` | `backend_type: str` | Switches active backend at runtime (`"sdrpp"` or `"mock"`). | `status: "ok"`, `active_backend` |
| `sdr_update_analysis` | `country`, `language`, `station`, `program`, `confidence`, `evidence`, `dialect` | Pushes AI station identification results back to the SDR++ UI console. | `success: True` |

---

## Project Structure

```
/Users/xanaduxuan/Documents/antigravity/sdr/
├── README.md                      # Project documentation (this file)
├── plugins/
│   └── sdrpp_agent/               # C++ Plugin for SDR++
│       ├── CMakeLists.txt
│       └── src/
│           └── main.cpp           # JSON-RPC server, audio ringbuffer, FFT bridge, ImGui UI
├── sdr_mcp/                       # Python Universal SDR MCP Server
│   ├── __init__.py
│   ├── __main__.py                # CLI entry point (--backend, --transport, --port)
│   ├── server.py                  # FastMCP tools definition
│   ├── backend/
│   │   ├── __init__.py            # Backend factory (get_backend)
│   │   ├── base.py                # Abstract SDRBackend ABC and dataclasses
│   │   ├── sdrpp.py               # Live SDR++ TCP JSON-RPC bridge
│   │   └── mock.py                # Pure software simulation backend
│   └── tests/
│       ├── test_mock_backend.py   # Mock backend unit tests (7 tests)
│       ├── test_sdrpp_backend.py  # Real SDR++ live IPC tests (8 tests)
│       └── test_mcp_tools.py      # MCP schema & tool invocation tests (10 tests)
└── repos/
    └── SDRPlusPlus/               # SDR++ source tree (used for plugin header compilation)
```

---

## Compiling & Installing the SDR++ Plugin

If modifications are made to `plugins/sdrpp_agent/src/main.cpp`:

```bash
mkdir -p /Users/xanaduxuan/Documents/antigravity/sdr/plugins/sdrpp_agent/build
cd /Users/xanaduxuan/Documents/antigravity/sdr/plugins/sdrpp_agent/build

cmake .. -DCMAKE_BUILD_TYPE=Release \
         -DSDRPP_MODULE_CMAKE_DIR=/Users/xanaduxuan/Documents/antigravity/sdr/repos/SDRPlusPlus/cmake

make -j$(sysctl -n hw.ncpu)

# Install into SDR++.app bundle
cp sdrpp_agent.dylib /Applications/SDR++.app/Contents/Plugins/
```
Restart SDR++ to apply changes.
