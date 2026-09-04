"""SDR Backend Registry and Factory."""

import os
from typing import Optional
from sdr_mcp.backend.base import SDRBackend, SDRStatus, SpectrumData, AudioSegment, RecordingInfo
from sdr_mcp.backend.sdrpp import SdrppBackend
from sdr_mcp.backend.mock import MockBackend

DEFAULT_BACKEND_NAME = os.environ.get("SDR_BACKEND", "sdrpp").lower()


def get_backend(backend_type: Optional[str] = None) -> SDRBackend:
    """Instantiate and return the requested SDR backend."""
    b_type = (backend_type or DEFAULT_BACKEND_NAME).lower()
    if b_type == "mock":
        return MockBackend()
    elif b_type == "sdrpp":
        return SdrppBackend()
    else:
        raise ValueError(f"Unknown SDR backend type '{b_type}'. Supported: 'sdrpp', 'mock'.")


__all__ = [
    "SDRBackend",
    "SdrppBackend",
    "MockBackend",
    "get_backend",
    "SDRStatus",
    "SpectrumData",
    "AudioSegment",
    "RecordingInfo",
]
