"""Universal SDR MCP Server - Abstract Backend Interface

Defines the abstract contract that every SDR backend (SDR++, RTL-TCP, Mock, etc.)
must satisfy. Backends must NEVER fake unsupported operations: if an operation
cannot be performed, return an explicit error or unsupported status.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


@dataclass
class SDRStatus:
    backend: str
    connected: bool
    frequency: float
    frequency_khz: float
    mode: str
    sample_rate: float
    audio_ready: bool
    vfo: str = ""
    active_device: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpectrumData:
    available: bool
    center_frequency: float
    bandwidth: float
    start_frequency: float
    end_frequency: float
    min_db: float
    max_db: float
    peak_db: float
    peak_frequency: float
    avg_db: float
    bin_count: int
    bins: List[float] = field(default_factory=list)
    backend: str = ""
    simulated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AudioSegment:
    success: bool
    path: str
    duration_sec: float
    sample_rate: int
    channels: int
    samples_recorded: int
    rms: float = 0.0
    peak: int = 0
    backend: str = ""
    simulated: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecordingInfo:
    status: str  # "started", "stopped", "in_progress", "error"
    path: str
    sample_rate: int
    channels: int
    duration_sec: float = 0.0
    samples_recorded: int = 0
    size_bytes: int = 0
    backend: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SDRBackend(ABC):
    """Abstract interface for all SDR control and data backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier (e.g. 'sdrpp', 'mock', 'rtl_tcp')."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if backend communication is currently established."""
        pass

    @abstractmethod
    def get_status(self) -> SDRStatus:
        """Retrieve current status of SDR backend."""
        pass

    @abstractmethod
    def get_devices(self) -> Dict[str, Any]:
        """List available devices or source plugins."""
        pass

    @abstractmethod
    def tune(self, frequency: float, mode: Optional[str] = None) -> Dict[str, Any]:
        """Tune SDR to a center frequency (Hz) and optionally set demodulation mode."""
        pass

    @abstractmethod
    def set_gain(self, gain_db: float) -> Dict[str, Any]:
        """Set RF gain in dB. Must return supported=False if not controllable."""
        pass

    @abstractmethod
    def set_sample_rate(self, sample_rate: float) -> Dict[str, Any]:
        """Set SDR sampling rate in Hz."""
        pass

    @abstractmethod
    def get_spectrum(self, bin_count: int = 256) -> SpectrumData:
        """Acquire latest RF spectrum FFT data."""
        pass

    @abstractmethod
    def get_audio(
        self,
        duration_sec: float = 5.0,
        frequency: Optional[float] = None,
        mode: Optional[str] = None
    ) -> AudioSegment:
        """Sample demodulated audio segment."""
        pass

    @abstractmethod
    def start_recording(self, path: Optional[str] = None) -> RecordingInfo:
        """Begin continuous recording of demodulated audio to WAV."""
        pass

    @abstractmethod
    def stop_recording(self) -> RecordingInfo:
        """Stop active continuous recording."""
        pass

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
        """Update backend UI or store station identification analysis results."""
        return {"backend": self.name, "supported": False}
