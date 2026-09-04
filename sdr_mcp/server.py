"""Universal SDR MCP Server

Exposes universal, hardware-agnostic SDR operations to any MCP client (AntiGravity,
Claude Desktop, Codex, Kimi, etc.) over the Model Context Protocol.

Supports interchangeable backends (SDR++, Mock, and future RTL-TCP / SoapySDR).
"""

import os
import sys
from typing import Optional, List, Dict, Any
from fastmcp import FastMCP

from sdr_mcp.backend import get_backend, SDRBackend

# Global backend instance
_active_backend: SDRBackend = get_backend()

mcp = FastMCP("universal-sdr")


def set_active_backend(backend_name: str) -> None:
    """Switch active SDR backend at runtime."""
    global _active_backend
    _active_backend = get_backend(backend_name)


def get_active_backend() -> SDRBackend:
    """Get reference to currently active backend."""
    return _active_backend


@mcp.tool()
def sdr_status() -> Dict[str, Any]:
    """Query current status of the active SDR backend, connection state, tuned frequency, mode, and audio readiness."""
    return _active_backend.get_status().to_dict()


@mcp.tool()
def sdr_devices() -> Dict[str, Any]:
    """List available radio hardware sources and input drivers managed by the backend."""
    return _active_backend.get_devices()


@mcp.tool()
def sdr_tune(frequency: float, mode: Optional[str] = None) -> Dict[str, Any]:
    """Tune SDR to a target center frequency in Hz and optionally set demodulator mode.
    
    Args:
        frequency: Target frequency in Hz (e.g. 11576000 for 11.576 MHz, 107300000 for 107.3 MHz)
        mode: Optional demodulation mode (AM, WFM, NFM, USB, LSB, CW, DSB, RAW)
    """
    return _active_backend.tune(frequency=frequency, mode=mode)


@mcp.tool()
def sdr_set_gain(gain_db: float) -> Dict[str, Any]:
    """Set SDR receiver RF frontend gain in dB.
    
    Args:
        gain_db: Gain value in dB (e.g. 29.7, 42.0)
    """
    return _active_backend.set_gain(gain_db=gain_db)


@mcp.tool()
def sdr_set_sample_rate(sample_rate: float) -> Dict[str, Any]:
    """Set SDR frontend input sampling rate in Hz.
    
    Args:
        sample_rate: Sampling rate in Hz (e.g. 2048000 for 2.048 MSPS, 2400000 for 2.4 MSPS)
    """
    return _active_backend.set_sample_rate(sample_rate=sample_rate)


@mcp.tool()
def sdr_get_spectrum(bin_count: int = 256) -> Dict[str, Any]:
    """Acquire real-time RF power spectrum FFT data across the receiver passband.
    
    Args:
        bin_count: Number of frequency bins to return (default: 256)
    """
    return _active_backend.get_spectrum(bin_count=bin_count).to_dict()


@mcp.tool()
def sdr_get_audio(
    duration_sec: float = 5.0,
    frequency: Optional[float] = None,
    mode: Optional[str] = None
) -> Dict[str, Any]:
    """Capture a real-time segment of demodulated audio from the SDR receiver.
    
    Args:
        duration_sec: Recording duration in seconds (default 5.0, min 1.0, max 60.0)
        frequency: Optional target frequency to tune before capturing audio
        mode: Optional demodulation mode to set before capturing audio
    """
    clamped_dur = max(1.0, min(60.0, duration_sec))
    return _active_backend.get_audio(
        duration_sec=clamped_dur,
        frequency=frequency,
        mode=mode
    ).to_dict()


@mcp.tool()
def sdr_start_recording(path: Optional[str] = None) -> Dict[str, Any]:
    """Start continuous recording of demodulated audio to a WAV file.
    
    Args:
        path: Optional destination file path on host (default: /tmp/sdr_recording_<timestamp>.wav)
    """
    return _active_backend.start_recording(path=path).to_dict()


@mcp.tool()
def sdr_stop_recording() -> Dict[str, Any]:
    """Stop active continuous audio recording and return file summary."""
    return _active_backend.stop_recording().to_dict()


@mcp.tool()
def sdr_switch_backend(backend_type: str) -> Dict[str, Any]:
    """Switch the SDR backend (e.g. 'sdrpp', 'mock').
    
    Args:
        backend_type: Name of backend to activate ('sdrpp' or 'mock')
    """
    try:
        set_active_backend(backend_type)
        return {"status": "ok", "active_backend": _active_backend.name}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def sdr_update_analysis(
    country: str,
    language: str,
    station: str,
    program: str,
    confidence: float,
    evidence: Optional[List[str]] = None,
    dialect: str = "",
) -> Dict[str, Any]:
    """Update SDR UI console with signal identification results (country, language, station, confidence, evidence).
    
    Args:
        country: Deduced country or region (e.g. 'China', 'Japan', 'North Korea')
        language: Identified language (e.g. 'Mandarin', 'Korean', 'Japanese')
        station: Suspected radio station or broadcaster name
        program: Suspected program type or title
        confidence: Numeric confidence score between 0.0 and 1.0
        evidence: List of auditory/linguistic observations supporting the identification
        dialect: Optional accent, dialect, or vocal style notes
    """
    return _active_backend.update_analysis(
        country=country,
        language=language,
        station=station,
        program=program,
        confidence=confidence,
        evidence=evidence or [],
        dialect=dialect,
    )


@mcp.tool()
def sdr_scan(
    start_frequency: float,
    end_frequency: float,
    step_hz: Optional[float] = None,
    dwell_ms: float = 150.0,
    mode: Optional[str] = None,
    min_snr_db: float = 6.0,
    threshold_db: Optional[float] = None,
    cluster_width_hz: float = 8000.0,
) -> Dict[str, Any]:
    """Perform an autonomous RF spectrum sweep across a frequency range to detect candidate signals.

    Args:
        start_frequency: Start frequency in Hz (e.g. 11000000 for 11.0 MHz)
        end_frequency: End frequency in Hz (e.g. 12500000 for 12.5 MHz)
        step_hz: Center frequency shift between windows in Hz. Defaults to 50% instantaneous bandwidth.
        dwell_ms: Dwell time in milliseconds at each frequency window before sampling FFT (default 150ms)
        mode: Optional demodulation mode during sweep (e.g. AM, USB, LSB)
        min_snr_db: Minimum peak SNR above noise floor in dB (default 6.0 dB)
        threshold_db: Optional absolute power threshold in dB (e.g. -70.0 dB)
        cluster_width_hz: Maximum distance in Hz to cluster adjacent spectral peaks (default 8000 Hz)
    """
    return _active_backend.scan(
        start_frequency=start_frequency,
        end_frequency=end_frequency,
        step_hz=step_hz,
        dwell_ms=dwell_ms,
        mode=mode,
        min_snr_db=min_snr_db,
        threshold_db=threshold_db,
        cluster_width_hz=cluster_width_hz,
    ).to_dict()


if __name__ == "__main__":
    mcp.run()

