"""Mock SDR Backend for Pure Software Testing

Allows end-to-end MCP software testing without requiring RTL-SDR hardware or
active radio frequency propagation. Explicitly identifies itself as 'mock'
in all outputs to prevent test results from ever being confused with real RF signals.
"""

import os
import time
import math
import wave
import struct
import random
from typing import Optional, Dict, Any, List

from sdr_mcp.backend.base import (
    SDRBackend,
    SDRStatus,
    SpectrumData,
    AudioSegment,
    RecordingInfo,
)


class MockBackend(SDRBackend):
    """Pure software simulation backend for testing and CI."""

    def __init__(self, initial_frequency: float = 10000000.0, mode: str = "AM"):
        self._connected = True
        self._frequency = initial_frequency
        self._mode = mode
        self._sample_rate = 2048000.0
        self._gain_db = 29.7
        self._bandwidth = 2000000.0

        # Recording state
        self._is_recording = False
        self._recording_path = ""
        self._recording_start_time = 0.0

    @property
    def name(self) -> str:
        return "mock"

    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, connected: bool) -> None:
        """Helper to simulate disconnects during tests."""
        self._connected = connected

    def get_status(self) -> SDRStatus:
        if not self._connected:
            return SDRStatus(
                backend=self.name,
                connected=False,
                frequency=0.0,
                frequency_khz=0.0,
                mode="DISCONNECTED",
                sample_rate=0.0,
                audio_ready=False,
                active_device="None (Mock Disconnected)",
                details={"simulated": True, "note": "Mock backend is currently disconnected"},
            )

        return SDRStatus(
            backend=self.name,
            connected=True,
            frequency=self._frequency,
            frequency_khz=self._frequency / 1000.0,
            mode=self._mode,
            sample_rate=self._sample_rate,
            audio_ready=True,
            vfo="MockVFO-0",
            active_device="Simulated Software SDR",
            details={
                "simulated": True,
                "gain_db": self._gain_db,
                "bandwidth_hz": self._bandwidth,
                "warning": "MOCK BACKEND: This is simulated data, not real RTL-SDR reception.",
            },
        )

    def get_devices(self) -> Dict[str, Any]:
        return {
            "backend": self.name,
            "connected": self._connected,
            "simulated": True,
            "active_device": "Simulated Software SDR",
            "available_sources": ["Mock Synthetic Generator", "Mock IQ Player"],
            "count": 2,
        }

    def tune(self, frequency: float, mode: Optional[str] = None) -> Dict[str, Any]:
        if not self._connected:
            return {"backend": self.name, "success": False, "error": "Mock backend disconnected"}

        self._frequency = float(frequency)
        if mode:
            self._mode = mode.upper()

        return {
            "backend": self.name,
            "simulated": True,
            "success": True,
            "frequency": self._frequency,
            "mode": self._mode,
        }

    def set_gain(self, gain_db: float) -> Dict[str, Any]:
        if not self._connected:
            return {"backend": self.name, "supported": True, "success": False, "error": "Disconnected"}

        self._gain_db = float(gain_db)
        return {
            "backend": self.name,
            "supported": True,
            "simulated": True,
            "success": True,
            "gain_db": self._gain_db,
        }

    def set_sample_rate(self, sample_rate: float) -> Dict[str, Any]:
        if not self._connected:
            return {"backend": self.name, "success": False, "error": "Disconnected"}

        self._sample_rate = float(sample_rate)
        return {
            "backend": self.name,
            "supported": True,
            "simulated": True,
            "success": True,
            "sample_rate": self._sample_rate,
        }

    def get_spectrum(self, bin_count: int = 256) -> SpectrumData:
        if not self._connected:
            return SpectrumData(
                available=False,
                center_frequency=0.0,
                bandwidth=0.0,
                start_frequency=0.0,
                end_frequency=0.0,
                min_db=-100.0,
                max_db=0.0,
                peak_db=-100.0,
                peak_frequency=0.0,
                avg_db=-100.0,
                bin_count=0,
                bins=[],
                backend=self.name,
                simulated=True,
            )

        # Generate synthetic spectrum: baseline noise floor around -85 dB
        # with a simulated carrier peak at center and small random harmonic peaks
        bins: List[float] = []
        center_bin = bin_count // 2
        carrier_width = max(2, bin_count // 40)

        for i in range(bin_count):
            noise = -88.0 + random.uniform(-3.0, 3.0)
            dist = abs(i - center_bin)
            if dist < carrier_width:
                # Simulated carrier peak around -35 dB
                peak_gain = 50.0 * math.exp(-0.5 * (dist / 1.5) ** 2)
                level = noise + peak_gain
            elif abs(i - (center_bin + bin_count // 5)) < 2:
                # Secondary harmonic peak around -60 dB
                level = noise + 25.0
            else:
                level = noise
            bins.append(round(level, 1))

        peak_db = max(bins)
        peak_idx = bins.index(peak_db)
        peak_freq = (self._frequency - self._bandwidth / 2.0) + (peak_idx / bin_count) * self._bandwidth

        return SpectrumData(
            available=True,
            center_frequency=self._frequency,
            bandwidth=self._bandwidth,
            start_frequency=self._frequency - self._bandwidth / 2.0,
            end_frequency=self._frequency + self._bandwidth / 2.0,
            min_db=-100.0,
            max_db=-20.0,
            peak_db=peak_db,
            peak_frequency=peak_freq,
            avg_db=round(sum(bins) / len(bins), 1),
            bin_count=bin_count,
            bins=bins,
            backend=self.name,
            simulated=True,
        )

    def get_audio(
        self,
        duration_sec: float = 5.0,
        frequency: Optional[float] = None,
        mode: Optional[str] = None,
    ) -> AudioSegment:
        if not self._connected:
            return AudioSegment(
                success=False,
                path="",
                duration_sec=0.0,
                sample_rate=48000,
                channels=1,
                samples_recorded=0,
                backend=self.name,
                simulated=True,
                error="Mock backend disconnected",
            )

        if frequency is not None:
            self.tune(frequency, mode)

        sample_rate = 48000
        num_samples = int(duration_sec * sample_rate)
        out_path = "/tmp/mock_sdr_audio.wav"

        # Generate synthetic 48kHz mono tone (440Hz + subtle noise)
        tone_freq = 440.0
        samples = []
        for i in range(num_samples):
            t = i / sample_rate
            val = int(8000.0 * math.sin(2.0 * math.pi * tone_freq * t) + random.uniform(-500.0, 500.0))
            samples.append(max(-32768, min(32767, val)))

        raw_bytes = struct.pack(f"<{len(samples)}h", *samples)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(raw_bytes)

        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        peak = max(abs(s) for s in samples)

        return AudioSegment(
            success=True,
            path=out_path,
            duration_sec=duration_sec,
            sample_rate=sample_rate,
            channels=1,
            samples_recorded=num_samples,
            rms=round(rms, 1),
            peak=peak,
            backend=self.name,
            simulated=True,
        )

    def start_recording(self, path: Optional[str] = None) -> RecordingInfo:
        if not self._connected:
            return RecordingInfo(
                status="error",
                path="",
                sample_rate=48000,
                channels=1,
                backend=self.name,
                error="Disconnected",
            )

        if self._is_recording:
            return RecordingInfo(
                status="error",
                path=self._recording_path,
                sample_rate=48000,
                channels=1,
                backend=self.name,
                error=f"Recording already in progress: {self._recording_path}",
            )

        self._is_recording = True
        self._recording_path = path or f"/tmp/mock_sdr_recording_{int(time.time())}.wav"
        self._recording_start_time = time.time()

        return RecordingInfo(
            status="started",
            path=self._recording_path,
            sample_rate=48000,
            channels=1,
            backend=self.name,
        )

    def stop_recording(self) -> RecordingInfo:
        if not self._is_recording:
            return RecordingInfo(
                status="error",
                path="",
                sample_rate=48000,
                channels=1,
                backend=self.name,
                error="No active recording to stop",
            )

        duration = time.time() - self._recording_start_time
        samples = int(duration * 48000)
        target_path = self._recording_path
        self._is_recording = False
        self._recording_path = ""

        # Write dummy WAV file for testing
        with wave.open(target_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"\x00\x00" * min(samples, 48000))

        file_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0

        return RecordingInfo(
            status="stopped",
            path=target_path,
            sample_rate=48000,
            channels=1,
            duration_sec=round(duration, 2),
            samples_recorded=samples,
            size_bytes=file_size,
            backend=self.name,
        )

    def update_analysis(
        self,
        country: str,
        language: str,
        station: str,
        program: str,
        confidence: float,
        evidence: Optional[List[str]] = None,
        dialect: str = "",
    ) -> Dict[str, Any]:
        self._last_analysis = {
            "country": country,
            "language": language,
            "station": station,
            "program": program,
            "confidence": confidence,
            "evidence": evidence or [],
            "dialect": dialect,
        }
        return {"backend": self.name, "success": True, "simulated": True}
