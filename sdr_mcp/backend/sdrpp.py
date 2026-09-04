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
from typing import Optional, Dict, Any, List

from sdr_mcp.backend.base import (
    SDRBackend,
    SDRStatus,
    SpectrumData,
    AudioSegment,
    RecordingInfo,
)

DEFAULT_IPC_HOST = os.environ.get("SDR_IPC_HOST", "127.0.0.1")
DEFAULT_IPC_PORT = int(os.environ.get("SDR_IPC_PORT", 19870))
DEFAULT_TIMEOUT = 12.0


class SdrppBackend(SDRBackend):
    """Bridge backend connecting to SDR++ via internal TCP JSON-RPC."""

    def __init__(self, host: str = DEFAULT_IPC_HOST, port: int = DEFAULT_IPC_PORT):
        self.host = host
        self.port = port

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

    def tune(self, frequency: float, mode: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"frequency": frequency}
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
        res = self._rpc_call("sdr_get_spectrum", timeout=3.0)
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
    ) -> AudioSegment:
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
