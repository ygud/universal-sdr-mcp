"""SDR++ Backend Implementation

Connects to SDR++ Agent Module via localhost TCP JSON-RPC (default 127.0.0.1:19870).
Respects hardware ownership: SDR++ is the sole owner of the RTL-SDR device.
"""

import os
import json
import socket
import time
import wave
import struct
import math
import threading
from typing import Optional, Dict, Any, List

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

DEFAULT_IPC_HOST = os.environ.get("SDR_IPC_HOST", "127.0.0.1")
DEFAULT_IPC_PORT = int(os.environ.get("SDR_IPC_PORT", 19870))
DEFAULT_TIMEOUT = 12.0


class SdrppBackend(SDRBackend):
    """Bridge backend connecting to SDR++ via internal TCP JSON-RPC."""

    def __init__(self, host: str = DEFAULT_IPC_HOST, port: int = DEFAULT_IPC_PORT):
        self.host = host
        self.port = port
        self._scan_lock = threading.Lock()
        self._is_scanning = False
        self._is_probing = False
        self._last_scan_candidates: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "sdrpp"

    def _rpc_call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """Send a JSON-RPC request to SDR++ plugin over localhost TCP."""
        if params is None:
            params = {}
        req = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
            "params": params,
        }

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect((self.host, self.port))
                payload = json.dumps(req) + "\n"
                s.sendall(payload.encode("utf-8"))

                chunks = []
                while True:
                    data = s.recv(65536)
                    if not data:
                        break
                    chunks.append(data)
                    if b"\n" in data:
                        break

                raw = b"".join(chunks).decode("utf-8").strip()
                if not raw:
                    return {"error": "Empty response from SDR++ plugin"}

                resp = json.loads(raw)
                if "error" in resp:
                    return {"error": resp["error"]}
                return resp.get("result", {})
        except ConnectionRefusedError:
            return {
                "error": f"Connection refused at {self.host}:{self.port}. Is SDR++ running with sdrpp_agent plugin loaded?"
            }
        except socket.timeout:
            return {"error": f"Socket timeout ({timeout:.1f}s) communicating with SDR++."}
        except Exception as e:
            return {"error": f"IPC exception: {str(e)}"}

    def is_connected(self) -> bool:
        res = self._rpc_call("sdr_health", timeout=2.0)
        return res.get("status") == "ok"

    def get_status(self) -> SDRStatus:
        res = self._rpc_call("sdr_status", timeout=3.0)
        if "error" in res:
            return SDRStatus(
                backend=self.name,
                connected=False,
                frequency=0.0,
                frequency_khz=0.0,
                mode="UNKNOWN",
                sample_rate=0.0,
                audio_ready=False,
                details={"error": res["error"]},
            )

        freq = float(res.get("frequency", 0.0))
        return SDRStatus(
            backend=self.name,
            connected=bool(res.get("connected", False)),
            frequency=freq,
            frequency_khz=freq / 1000.0,
            mode=str(res.get("mode", "UNKNOWN")),
            sample_rate=float(res.get("sample_rate", 48000.0)),
            audio_ready=bool(res.get("audio_ready", False)),
            vfo=str(res.get("vfo", "")),
            active_device="RTL-SDR (via SDR++)",
            details={
                "audio_stream": res.get("audio_stream", ""),
                "plugin_version": res.get("version", ""),
            },
        )

    def get_devices(self) -> Dict[str, Any]:
        res = self._rpc_call("sdr_devices", timeout=3.0)
        if "error" in res:
            return {"backend": self.name, "connected": False, "error": res["error"]}
        return {
            "backend": self.name,
            "connected": True,
            "active_device": "RTL-SDR",
            "available_sources": res.get("available_sources", []),
            "count": res.get("count", 0),
        }

    def tune(
        self,
        frequency: float,
        mode: Optional[str] = None,
        center: bool = False,
        internal: bool = False,
    ) -> Dict[str, Any]:
        if not internal and (self._is_scanning or self._is_probing):
            return {"backend": self.name, "success": False, "error": "Hardware busy: SDR scan or probe in progress"}
        params: Dict[str, Any] = {"frequency": frequency, "center": center}
        if mode:
            params["mode"] = mode.upper()
        res = self._rpc_call("sdr_tune", params, timeout=4.0)
        if "error" in res:
            return {"backend": self.name, "success": False, "error": res["error"]}
        return {
            "backend": self.name,
            "success": bool(res.get("success", False)),
            "frequency": res.get("frequency", frequency),
            "mode": res.get("mode", mode or "UNCHANGED"),
            "center": bool(res.get("center", center)),
        }

    def set_gain(self, gain_db: float) -> Dict[str, Any]:
        res = self._rpc_call("sdr_set_gain", {"gain_db": gain_db}, timeout=3.0)
        if "error" in res:
            return {"backend": self.name, "supported": False, "error": res["error"]}
        return res

    def set_sample_rate(self, sample_rate: float) -> Dict[str, Any]:
        res = self._rpc_call("sdr_set_sample_rate", {"sample_rate": sample_rate}, timeout=4.0)
        if "error" in res:
            return {"backend": self.name, "success": False, "error": res["error"]}
        return res

    def get_spectrum(self, bin_count: int = 256) -> SpectrumData:
        res = self._rpc_call("sdr_get_spectrum", {"bin_count": bin_count}, timeout=3.0)
        if "error" in res or not res.get("available", False):
            err_msg = res.get("error", "Spectrum data unavailable")
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
                simulated=False,
            )

        return SpectrumData(
            available=True,
            center_frequency=float(res.get("center_frequency", 0.0)),
            bandwidth=float(res.get("bandwidth", 0.0)),
            start_frequency=float(res.get("start_frequency", 0.0)),
            end_frequency=float(res.get("end_frequency", 0.0)),
            min_db=float(res.get("min_db", -100.0)),
            max_db=float(res.get("max_db", 0.0)),
            peak_db=float(res.get("peak_db", -100.0)),
            peak_frequency=float(res.get("peak_frequency", 0.0)),
            avg_db=float(res.get("avg_db", -100.0)),
            bin_count=int(res.get("bin_count", len(res.get("bins", [])))),
            bins=res.get("bins", []),
            backend=self.name,
            simulated=False,
        )

    def get_audio(
        self,
        duration_sec: float = 5.0,
        frequency: Optional[float] = None,
        mode: Optional[str] = None,
        internal: bool = False,
    ) -> AudioSegment:
        if not internal and (self._is_scanning or self._is_probing):
            return AudioSegment(
                success=False,
                path="",
                duration_sec=0.0,
                sample_rate=48000,
                channels=1,
                samples_recorded=0,
                backend=self.name,
                simulated=False,
                error="Hardware busy: SDR scan or probe in progress",
            )
        if frequency is not None:
            self.tune(frequency, mode)
            time.sleep(0.3)

        timeout = max(duration_sec + 5.0, 15.0)
        res = self._rpc_call("sdr_sample_audio", {"duration_sec": duration_sec}, timeout=timeout)

        if "error" in res or not res.get("success", False):
            return AudioSegment(
                success=False,
                path="",
                duration_sec=0.0,
                sample_rate=48000,
                channels=1,
                samples_recorded=0,
                backend=self.name,
                simulated=False,
                error=res.get("error", "Audio sampling failed or produced 0 samples"),
            )

        path = res.get("path", "/tmp/sdr_sample.wav")
        samples_count = res.get("samples_recorded", 0)
        sr = res.get("sample_rate", 48000)

        # Calculate RMS and Peak from actual WAV file
        rms = 0.0
        peak = 0
        if os.path.exists(path):
            try:
                with wave.open(path, "rb") as w:
                    frames = w.readframes(w.getnframes())
                    if len(frames) >= 2:
                        samples = struct.unpack(f"<{len(frames)//2}h", frames)
                        if samples:
                            rms = math.sqrt(sum(s * s for s in samples) / len(samples))
                            peak = max(abs(s) for s in samples)
            except Exception:
                pass

        return AudioSegment(
            success=True,
            path=path,
            duration_sec=res.get("duration_sec", duration_sec),
            sample_rate=sr,
            channels=res.get("channels", 1),
            samples_recorded=samples_count,
            rms=round(rms, 1),
            peak=peak,
            backend=self.name,
            simulated=False,
        )

    def start_recording(self, path: Optional[str] = None) -> RecordingInfo:
        params = {}
        if path:
            params["path"] = path
        res = self._rpc_call("sdr_start_recording", params, timeout=4.0)
        if "error" in res:
            return RecordingInfo(
                status="error",
                path=path or "",
                sample_rate=48000,
                channels=1,
                backend=self.name,
                error=res["error"],
            )

        return RecordingInfo(
            status="started",
            path=res.get("path", ""),
            sample_rate=res.get("sample_rate", 48000),
            channels=res.get("channels", 1),
            backend=self.name,
        )

    def stop_recording(self) -> RecordingInfo:
        res = self._rpc_call("sdr_stop_recording", timeout=4.0)
        if "error" in res:
            return RecordingInfo(
                status="error",
                path="",
                sample_rate=48000,
                channels=1,
                backend=self.name,
                error=res["error"],
            )

        return RecordingInfo(
            status="stopped",
            path=res.get("path", ""),
            sample_rate=res.get("sample_rate", 48000),
            channels=1,
            duration_sec=round(res.get("duration_sec", 0.0), 2),
            samples_recorded=res.get("samples_recorded", 0),
            size_bytes=res.get("size_bytes", 0),
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
        params = {
            "country": country,
            "language": language,
            "station": station,
            "program": program,
            "confidence": confidence,
            "evidence": evidence or [],
            "dialect": dialect,
        }
        res = self._rpc_call("sdr_update_analysis", params, timeout=3.0)
        if "error" in res:
            return {"backend": self.name, "success": False, "error": res["error"]}
        return {"backend": self.name, "success": bool(res.get("success", False))}

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
        params = {
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
        res = self._rpc_call("sdr_update_scan_status", params, timeout=2.0)
        if "error" in res:
            return {"backend": self.name, "success": False, "error": res["error"]}
        return {"backend": self.name, "success": bool(res.get("success", True))}

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
        if not orig_status.connected:
            self._is_scanning = False
            return ScanResult(
                success=False,
                start_frequency=start_frequency,
                end_frequency=end_frequency,
                window_count=0,
                elapsed_sec=0.0,
                error="SDR++ backend is disconnected",
            )

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

        # Query initial spectrum to detect actual hardware bandwidth
        spec0 = self.get_spectrum(bin_count=256)
        if spec0.available and spec0.bandwidth > 0:
            window_bw = spec0.bandwidth
        else:
            window_bw = 2048000.0  # Safe default 2.048 MHz

        max_step = window_bw * 0.5  # Safe 50% overlap rule
        warning = None
        if step_hz is None:
            step_hz = max_step
        elif step_hz > max_step:
            warning = f"Requested step_hz {step_hz} exceeds safe 50% overlap limit ({max_step}); clamped to {max_step}."
            step_hz = max_step

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
                    continue  # MUST NOT use stale data from previous window!

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
                    mask_dc_bins=2 if total_windows > 1 else 0,
                )
                cands = cluster_adjacent_peaks(peaks, cluster_width_hz=cluster_width_hz)
                all_raw_candidates.extend(cands)

            # Deduplicate across overlapping windows
            deduped = deduplicate_candidates(all_raw_candidates, min_distance_hz=cluster_width_hz * 0.75)
            # Filter strictly within requested range [start_frequency, end_frequency]
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

            # Sync completed candidates back to SDR++ Agent Console
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
                "simulated": False,
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
            # RESTORATION GUARANTEE: restore original center frequency and mode
            try:
                if orig_freq > 0:
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

        # Resolve candidate pool (provided or cached from last scan)
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
                # 1. Tune to candidate frequency with center=True for optimal passband centering
                t_res = self.tune(freq, mode="AM", center=True, internal=True)
                if not t_res.get("success", False):
                    continue  # Failure isolation: never reuse stale data

                time.sleep(0.15)  # Short settling time

                # 2. Local spectrum re-acquisition
                spec = self.get_spectrum(bin_count=256)
                if not spec.available or not spec.bins:
                    continue
                step_hz = (spec.bandwidth / len(spec.bins)) if len(spec.bins) > 0 else 8437.5
                rf_feat = extract_rf_features(
                    bins=spec.bins,
                    target_freq=freq,
                    start_freq=spec.start_frequency,
                    step_hz=step_hz,
                )

                # 3. Demodulated audio probe
                aud = self.get_audio(duration_sec=probe_duration_sec, internal=True)
                if not aud.success:
                    continue
                audio_feat = extract_audio_features(aud.path)

                # 4. Multi-evidence fusion and classification
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
                    self.tune(orig_freq, mode=orig_mode, center=True, internal=True)
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

