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
import threading

from sdr_mcp.backend.base import (
    SDRBackend,
    SDRStatus,
    SpectrumData,
    AudioSegment,
    RecordingInfo,
    ScanCandidate,
    ScanResult,
    ScreenedSignal,
    ScreenResult,
)
from sdr_mcp.detector import (
    estimate_noise_floor,
    detect_peaks_in_window,
    cluster_adjacent_peaks,
    deduplicate_candidates,
    filter_scan_range,
)
from sdr_mcp.screener import (
    AudioFeature,
    RFFeature,
    extract_audio_features,
    extract_rf_features,
    compute_scores_and_classify,
    rank_and_select_candidates,
)

DEFAULT_SYNTHETIC_SIGNALS = [
    {"frequency": 11204000.0, "power_db": -42.0, "bandwidth_hz": 9000.0, "sidebands": [(-2500, -58.0), (2500, -58.0)], "signal_type": "broadcast"},
    {"frequency": 11760000.0, "power_db": -55.0, "bandwidth_hz": 6000.0, "sidebands": [], "signal_type": "broadcast"},
    {"frequency": 11960000.0, "power_db": -68.0, "bandwidth_hz": 5000.0, "sidebands": [], "signal_type": "weak"},
    {"frequency": 14200000.0, "power_db": -48.0, "bandwidth_hz": 3000.0, "sidebands": [], "signal_type": "carrier_only"},
]



class MockBackend(SDRBackend):
    """Pure software simulation backend for testing and CI."""

    def __init__(self, initial_frequency: float = 10000000.0, mode: str = "AM"):
        self._connected = True
        self._frequency = initial_frequency
        self._mode = mode
        self._sample_rate = 2048000.0
        self._gain_db = 29.7
        self._bandwidth = 2000000.0

        # Scanning and concurrency lock
        self._is_scanning = False
        self._is_probing = False
        self._scan_lock = threading.Lock()
        self._scan_status: Dict[str, Any] = {}
        self._fail_tune_freq: Optional[float] = None
        self._synthetic_signals: List[Dict[str, Any]] = [s.copy() for s in DEFAULT_SYNTHETIC_SIGNALS]
        self._last_scan_candidates: List[Dict[str, Any]] = []

        # Recording state
        self._is_recording = False
        self._recording_path = ""
        self._recording_start_time = 0.0

    def set_synthetic_signals(self, signals: List[Dict[str, Any]]) -> None:
        """Configure mock RF signals for testing."""
        self._synthetic_signals = [s.copy() for s in signals]

    def set_tune_failure(self, frequency: Optional[float]) -> None:
        """Simulate a tune failure at a specific frequency for testing."""
        self._fail_tune_freq = frequency

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

    def tune(self, frequency: float, mode: Optional[str] = None, center: bool = False, internal: bool = False) -> Dict[str, Any]:
        if not self._connected:
            return {"backend": self.name, "success": False, "error": "Mock backend disconnected"}

        if (self._is_scanning or self._is_probing) and not internal:
            return {"backend": self.name, "success": False, "error": "SDR is currently busy scanning or probing"}

        if self._fail_tune_freq is not None and abs(frequency - self._fail_tune_freq) < 1000.0:
            return {"backend": self.name, "success": False, "error": f"Simulated tune failure at {frequency} Hz"}

        self._frequency = float(frequency)
        if mode:
            self._mode = mode.upper()

        return {
            "backend": self.name,
            "simulated": True,
            "success": True,
            "frequency": self._frequency,
            "mode": self._mode,
            "center": center,
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

        start_f = self._frequency - self._bandwidth / 2.0
        step_hz = self._bandwidth / bin_count

        bins: List[float] = []
        for i in range(bin_count):
            f_bin = start_f + i * step_hz
            # Deterministic noise floor ~ -100 dB (Requirement 11)
            noise = -100.0 + 1.5 * math.sin(i * 0.23 + (self._frequency / 1e6))
            pwr = noise

            for sig in self._synthetic_signals:
                sig_f = sig["frequency"]
                sig_pwr = sig["power_db"]
                sig_bw = sig.get("bandwidth_hz", 6000.0)

                # Carrier contribution (Gaussian profile from noise floor up to peak dB)
                sigma = sig_bw / 2.355
                dist = abs(f_bin - sig_f)
                if dist < sig_bw * 2.0:
                    delta_db = (sig_pwr - noise) * math.exp(-0.5 * (dist / max(1.0, sigma)) ** 2)
                    carrier_pwr = noise + delta_db
                    pwr = max(pwr, carrier_pwr)

                # Sidebands if any (e.g. AM modulation sidebands)
                for sb_offset, sb_pwr in sig.get("sidebands", []):
                    sb_f = sig_f + sb_offset
                    sb_dist = abs(f_bin - sb_f)
                    if sb_dist < step_hz * 2.0:
                        sb_delta = (sb_pwr - noise) * math.exp(-0.5 * (sb_dist / max(1.0, step_hz)) ** 2)
                        pwr = max(pwr, noise + sb_delta)

            bins.append(round(pwr, 1))

        peak_db = max(bins)
        peak_idx = bins.index(peak_db)
        peak_freq = start_f + peak_idx * step_hz

        return SpectrumData(
            available=True,
            center_frequency=self._frequency,
            bandwidth=self._bandwidth,
            start_frequency=start_f,
            end_frequency=start_f + self._bandwidth,
            min_db=-110.0,
            max_db=-20.0,
            peak_db=peak_db,
            peak_frequency=round(peak_freq, 1),
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
        internal: bool = False,
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

        if (self._is_scanning or self._is_probing) and not internal:
            return AudioSegment(
                success=False,
                path="",
                duration_sec=0.0,
                sample_rate=48000,
                channels=1,
                samples_recorded=0,
                backend=self.name,
                simulated=True,
                error="SDR is currently busy scanning or probing",
            )

        if frequency is not None:
            t_res = self.tune(frequency, mode, internal=internal)
            if not t_res.get("success", False):
                return AudioSegment(
                    success=False,
                    path="",
                    duration_sec=0.0,
                    sample_rate=48000,
                    channels=1,
                    samples_recorded=0,
                    backend=self.name,
                    simulated=True,
                    error=t_res.get("error", "Tune failed"),
                )

        sample_rate = 48000
        num_samples = int(duration_sec * sample_rate)
        out_path = "/tmp/mock_sdr_audio.wav"

        # Check proximity to configured synthetic signals
        target_sig = None
        for s in self._synthetic_signals:
            if abs(self._frequency - s["frequency"]) < 10000.0:
                target_sig = s
                break

        sig_type = target_sig.get("signal_type", "broadcast") if target_sig else "noise"
        samples = []

        if sig_type == "broadcast":
            # Modulated speech-like envelope with 3.2 Hz syllable rhythm (CV ~0.065)
            for i in range(num_samples):
                t = i / sample_rate
                env = 0.55 + 0.35 * math.sin(2.0 * math.pi * 3.2 * t)
                if (int(t * 1.8) % 3) == 0:
                    env *= 0.35
                tone = math.sin(2.0 * math.pi * 440.0 * t) + 0.35 * math.sin(2.0 * math.pi * 880.0 * t)
                val = int(env * 14000.0 * tone + random.gauss(0, 400.0))
                samples.append(max(-32768, min(32767, val)))
        elif sig_type == "carrier_only":
            # Pure unmodulated carrier: flat low noise floor, CV < 0.03
            for i in range(num_samples):
                val = int(random.gauss(0, 250.0))
                samples.append(max(-32768, min(32767, val)))
        elif sig_type == "pause":
            # Short speech pause: carrier present, audio low
            for i in range(num_samples):
                val = int(random.gauss(0, 350.0))
                samples.append(max(-32768, min(32767, val)))
        elif sig_type == "weak":
            # Weak broadcast signal with lower SNR
            for i in range(num_samples):
                t = i / sample_rate
                env = 0.50 + 0.25 * math.sin(2.0 * math.pi * 2.5 * t)
                tone = math.sin(2.0 * math.pi * 550.0 * t)
                val = int(env * 3500.0 * tone + random.gauss(0, 1800.0))
                samples.append(max(-32768, min(32767, val)))
        else:
            # White noise / empty channel: flat Gaussian noise, CV around 0.025
            for i in range(num_samples):
                val = int(random.gauss(0, 4500.0))
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

    def update_scan_status(
        self,
        status: str = "IDLE",
        scanning: bool = False,
        start_frequency: float = 0.0,
        end_frequency: float = 0.0,
        current_frequency: float = 0.0,
        progress: float = 0.0,
        found_candidates: int = 0,
        noise_floor_db: float = -100.0,
        candidates: Optional[List[Dict[str, Any]]] = None,
        error_message: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self._scan_status = {
            "status": status,
            "scanning": scanning or (status == "SCANNING"),
            "start_frequency": start_frequency,
            "end_frequency": end_frequency,
            "current_frequency": current_frequency,
            "progress": progress,
            "found_candidates": found_candidates,
            "noise_floor_db": noise_floor_db,
            "candidates": candidates if candidates is not None else [],
            "error_message": error_message,
        }
        return {"backend": self.name, "success": True, "simulated": True}

    def scan(
        self,
        start_frequency: float,
        end_frequency: float,
        step_hz: Optional[float] = None,
        dwell_ms: float = 150.0,
        mode: Optional[str] = None,
        min_snr_db: float = 6.0,
        threshold_db: Optional[float] = None,
        cluster_width_hz: float = 8000.0,
    ) -> ScanResult:
        if end_frequency <= start_frequency:
            raise ValueError(f"end_frequency ({end_frequency}) must be greater than start_frequency ({start_frequency})")

        with self._scan_lock:
            if self._is_scanning:
                return ScanResult(
                    success=False,
                    start_frequency=start_frequency,
                    end_frequency=end_frequency,
                    window_count=0,
                    elapsed_sec=0.0,
                    error="SDR is currently busy scanning",
                )
            self._is_scanning = True

        start_time = time.time()
        orig_status = self.get_status()
        orig_freq = orig_status.frequency
        orig_mode = orig_status.mode

        # Reset previous UI results when a new scan starts
        self.update_scan_status(
            status="SCANNING",
            scanning=True,
            start_frequency=start_frequency,
            end_frequency=end_frequency,
            current_frequency=start_frequency,
            found_candidates=0,
            candidates=[],
        )

        window_bw = self._bandwidth
        max_step = window_bw * 0.5  # Safe 50% overlap rule (Requirement 2)
        warning = None
        if step_hz is None:
            step_hz = max_step
        elif step_hz > max_step:
            warning = f"Requested step_hz {step_hz} exceeds safe 50% overlap limit ({max_step}); clamped to {max_step}."
            step_hz = max_step

        # Generate window centers with safe overlap
        span = end_frequency - start_frequency
        if span <= step_hz:
            window_centers = [(start_frequency + end_frequency) / 2.0]
        else:
            first_center = start_frequency + step_hz / 2.0
            window_centers = []
            curr = first_center
            while (curr - step_hz / 2.0) < end_frequency:
                window_centers.append(curr)
                curr += step_hz

        all_raw_candidates: List[Dict[str, Any]] = []
        failed_windows: List[Dict[str, Any]] = []
        measured_noise_floors: List[float] = []
        scan_completed = False

        try:
            total_windows = len(window_centers)
            for idx, center_f in enumerate(window_centers):
                self.update_scan_status(
                    status="SCANNING",
                    scanning=True,
                    start_frequency=start_frequency,
                    end_frequency=end_frequency,
                    current_frequency=center_f,
                    progress=round(idx / max(1, total_windows), 2),
                    found_candidates=len(all_raw_candidates),
                )

                # Tune with center=True, internal=True
                t_res = self.tune(center_f, mode=mode, center=True, internal=True)
                if not t_res.get("success", False):
                    failed_windows.append({
                        "window_idx": idx,
                        "center_freq": center_f,
                        "error": t_res.get("error", "Tune failed"),
                    })
                    continue  # MUST NOT use stale data from previous window! (Requirement 6)

                # Dwell
                if dwell_ms > 0:
                    time.sleep(dwell_ms / 1000.0)

                # Acquire spectrum
                spec = self.get_spectrum(bin_count=512)
                if not spec.available or not spec.bins:
                    failed_windows.append({
                        "window_idx": idx,
                        "center_freq": center_f,
                        "error": "Spectrum acquisition failed",
                    })
                    continue

                nf = estimate_noise_floor(spec.bins)
                measured_noise_floors.append(nf)
                step_per_bin = spec.bandwidth / len(spec.bins)
                peaks = detect_peaks_in_window(
                    bins=spec.bins,
                    start_freq=spec.start_frequency,
                    step_hz=step_per_bin,
                    noise_floor_db=nf,
                    min_snr_db=min_snr_db,
                    threshold_db=threshold_db,
                )
                cands = cluster_adjacent_peaks(peaks, cluster_width_hz=cluster_width_hz)
                all_raw_candidates.extend(cands)

            # Deduplicate across overlapping windows
            deduped = deduplicate_candidates(all_raw_candidates, min_distance_hz=cluster_width_hz * 0.75)
            # Filter strictly within requested range [start_frequency, end_frequency] (Requirement 8)
            final_cands_dict = filter_scan_range(deduped, start_frequency, end_frequency)

            scan_candidates = [
                ScanCandidate(
                    frequency=c["frequency"],
                    power_db=c["power_db"],
                    estimated_snr_db=c["estimated_snr_db"],
                    bandwidth_hz=c["bandwidth_hz"],
                    confidence=c["confidence"],
                )
                for c in final_cands_dict
            ]

            avg_nf = round(sum(measured_noise_floors) / len(measured_noise_floors), 1) if measured_noise_floors else -100.0
            elapsed = round(time.time() - start_time, 2)

            is_success = len(failed_windows) < total_windows
            err_msg = "All scan windows failed" if not is_success else None

            candidate_dicts = [
                {
                    "frequency": c.frequency,
                    "snr_db": c.estimated_snr_db,
                    "power_db": c.power_db,
                    "bandwidth_hz": c.bandwidth_hz,
                    "confidence": c.confidence,
                    "status": "Candidate",
                }
                for c in scan_candidates
            ]

            if is_success:
                self._last_scan_candidates = list(candidate_dicts)
                self.update_scan_status(
                    status="COMPLETE",
                    scanning=False,
                    start_frequency=start_frequency,
                    end_frequency=end_frequency,
                    noise_floor_db=avg_nf,
                    found_candidates=len(scan_candidates),
                    candidates=candidate_dicts,
                )
                scan_completed = True
            else:
                self.update_scan_status(
                    status="FAILED",
                    scanning=False,
                    start_frequency=start_frequency,
                    end_frequency=end_frequency,
                    error_message=err_msg or "Scan failed",
                    candidates=[],
                )
                scan_completed = True

            details: Dict[str, Any] = {
                "step_hz": step_hz,
                "window_bandwidth": window_bw,
                "simulated": True,
            }
            if failed_windows:
                details["failed_windows"] = failed_windows
            if warning:
                details["warning"] = warning

            return ScanResult(
                success=is_success,
                start_frequency=start_frequency,
                end_frequency=end_frequency,
                window_count=total_windows,
                elapsed_sec=elapsed,
                candidates=scan_candidates,
                noise_floor_db=avg_nf,
                details=details,
                error=err_msg,
            )

        finally:
            # RESTORATION GUARANTEE (Requirement 5)
            try:
                self.tune(orig_freq, mode=orig_mode, center=True, internal=True)
                if not scan_completed:
                    self.update_scan_status(
                        status="FAILED",
                        scanning=False,
                        start_frequency=start_frequency,
                        end_frequency=end_frequency,
                        error_message="Scan aborted or encountered unhandled exception",
                        candidates=[],
                    )
            finally:
                self._is_scanning = False

    def screen_candidates(
        self,
        candidates: Optional[List[Dict[str, Any]]] = None,
        max_probes: int = 12,
        probe_duration_sec: float = 1.0,
        min_score_threshold: float = 0.35,
        preserve_uncertain: bool = True,
    ) -> ScreenResult:
        """Algorithmic pre-screening of raw scan candidates down to high-value signals."""
        if self._is_scanning or self._is_probing:
            return ScreenResult(
                success=False,
                probed_count=0,
                retained_count=0,
                duration_sec=0.0,
                error="Hardware busy: SDR scan or probe in progress",
            )

        start_time = time.time()

        # Resolve candidate pool
        cand_pool = candidates if candidates is not None else list(self._last_scan_candidates)
        if not cand_pool:
            return ScreenResult(
                success=True,
                probed_count=0,
                retained_count=0,
                duration_sec=round(time.time() - start_time, 2),
                signals=[],
                details={"note": "No candidate signals provided or cached"},
            )

        # Pure RF mathematical prior ranking and selection
        selected = rank_and_select_candidates(
            cand_pool, max_probes=max_probes, min_spacing_hz=8000.0
        )
        if not selected:
            return ScreenResult(
                success=True,
                probed_count=0,
                retained_count=0,
                duration_sec=round(time.time() - start_time, 2),
                signals=[],
            )

        # Save original receiver status for 100% restoration guarantee
        orig_status = self.get_status()
        orig_freq = orig_status.frequency
        orig_mode = orig_status.mode

        self._is_probing = True
        probed_signals: List[ScreenedSignal] = []

        try:
            for c in selected:
                freq = float(c["frequency"])
                t_res = self.tune(freq, mode="AM", center=False, internal=True)
                if not t_res.get("success", False):
                    continue  # Failure isolation: never reuse stale data

                # Local spectrum re-acquisition
                spec = self.get_spectrum(bin_count=256)
                if not spec.available or not spec.bins:
                    continue
                step_hz = (spec.bandwidth / len(spec.bins)) if len(spec.bins) > 0 else 7812.5
                rf_feat = extract_rf_features(
                    bins=spec.bins,
                    target_freq=freq,
                    start_freq=spec.start_frequency,
                    step_hz=step_hz,
                )

                # Demodulated audio probe
                aud = self.get_audio(duration_sec=probe_duration_sec, internal=True)
                if not aud.success:
                    continue
                audio_feat = extract_audio_features(aud.path)

                # Multi-evidence fusion and classification
                b_score, conf, classification, rec = compute_scores_and_classify(
                    rf_feat, audio_feat
                )

                signal_obj = ScreenedSignal(
                    frequency=freq,
                    frequency_khz=round(freq / 1e3, 1),
                    classification=classification,
                    broadcast_score=b_score,
                    confidence=conf,
                    rf_evidence={
                        "local_snr_db": rf_feat.local_snr_db,
                        "prominence_db": rf_feat.prominence_db,
                        "power_db": rf_feat.power_db,
                        "symmetry_score": rf_feat.symmetry_score,
                        "rf_score": rf_feat.score,
                    },
                    audio_evidence={
                        "rms": audio_feat.rms,
                        "envelope_cv": audio_feat.envelope_cv,
                        "dynamic_range_db": audio_feat.dynamic_range_db,
                        "zcr_mean": audio_feat.zcr_mean,
                        "zcr_std": audio_feat.zcr_std,
                        "audio_score": audio_feat.score,
                    },
                    temporal_evidence=None,  # Reserved for v0.2
                    recommendation=rec,
                )
                probed_signals.append(signal_obj)

        finally:
            # RESTORATION GUARANTEE: unconditionally restore original receiver state
            try:
                if orig_freq > 0:
                    self.tune(orig_freq, mode=orig_mode, center=False, internal=True)
            finally:
                self._is_probing = False

        # Recall-First filtering policy
        retained: List[ScreenedSignal] = []
        for s in probed_signals:
            if s.classification == "BROADCAST_ACTIVE":
                retained.append(s)
            elif s.classification == "UNCERTAIN" and preserve_uncertain:
                retained.append(s)
            elif s.classification == "CARRIER_ONLY" and s.broadcast_score >= min_score_threshold:
                retained.append(s)
            # NOISE_STATIC and low-score CARRIER_ONLY are filtered out

        # Sort retained descending by broadcast_score
        retained.sort(key=lambda x: x.broadcast_score, reverse=True)
        elapsed = round(time.time() - start_time, 2)

        return ScreenResult(
            success=True,
            probed_count=len(probed_signals),
            retained_count=len(retained),
            duration_sec=elapsed,
            signals=retained,
            details={
                "probed_candidates": len(probed_signals),
                "max_probes": max_probes,
                "min_score_threshold": min_score_threshold,
                "preserve_uncertain": preserve_uncertain,
            },
        )
