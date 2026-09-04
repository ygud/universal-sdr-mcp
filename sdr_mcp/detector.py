"""RF Spectrum Peak Detector & Candidate Signal Extraction

Pure-signal processing routines for frequency sweep processing:
- Noise floor estimation (percentile-based)
- Local peak detection with relative SNR and absolute thresholding
- Adjacent peak clustering (merging sidebands into single carrier candidates)
- Cross-window candidate deduplication
- Range filtering

Strictly avoids any station identification, ASR, or metadata deduction.
All detections represent raw RF candidates only.
"""

from typing import List, Dict, Any, Optional
import math


def estimate_noise_floor(bins: List[float], percentile: float = 0.50) -> float:
    """Estimate background noise floor in dB using a robust percentile.
    
    Args:
        bins: List of FFT power values in dB.
        percentile: Quantile point (default 0.50 = median), robust against carriers.
    
    Returns:
        Estimated noise floor in dB.
    """
    if not bins:
        return -100.0
    
    sorted_bins = sorted(bins)
    k = max(0, min(len(sorted_bins) - 1, int(len(sorted_bins) * percentile)))
    return round(sorted_bins[k], 2)


def detect_peaks_in_window(
    bins: List[float],
    start_freq: float,
    step_hz: float,
    noise_floor_db: float,
    min_snr_db: float = 6.0,
    threshold_db: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Detect local maxima peaks exceeding noise floor and optional threshold.
    
    Args:
        bins: FFT power values across passband in dB.
        start_freq: Frequency of bin 0 in Hz.
        step_hz: Frequency span per bin in Hz.
        noise_floor_db: Estimated noise floor in dB.
        min_snr_db: Minimum estimated SNR (power - noise_floor) in dB.
        threshold_db: Optional absolute minimum power in dB.
    
    Returns:
        List of raw peak dictionaries with frequency, power_db, estimated_snr_db, bin_idx.
    """
    peaks: List[Dict[str, Any]] = []
    n = len(bins)
    if n < 3:
        return peaks

    for i in range(1, n - 1):
        pwr = bins[i]
        # Local maximum check
        if pwr <= bins[i - 1] or pwr < bins[i + 1]:
            continue

        # Power threshold check
        if threshold_db is not None and pwr < threshold_db:
            continue

        # SNR check
        snr = pwr - noise_floor_db
        if snr < min_snr_db:
            continue

        freq = start_freq + (i * step_hz)
        peaks.append({
            "frequency": round(freq, 1),
            "power_db": round(pwr, 2),
            "estimated_snr_db": round(snr, 2),
            "bin_idx": i,
        })

    return peaks


def cluster_adjacent_peaks(
    peaks: List[Dict[str, Any]],
    cluster_width_hz: float = 8000.0,
) -> List[Dict[str, Any]]:
    """Group neighboring peaks from modulation sidebands into single carrier candidates.
    
    Args:
        peaks: List of raw peak dictionaries sorted by frequency.
        cluster_width_hz: Maximum frequency distance between consecutive peaks in a cluster.
    
    Returns:
        List of clustered candidate dictionaries.
    """
    if not peaks:
        return []

    sorted_peaks = sorted(peaks, key=lambda x: x["frequency"])
    clusters: List[List[Dict[str, Any]]] = []
    current_cluster: List[Dict[str, Any]] = [sorted_peaks[0]]

    for p in sorted_peaks[1:]:
        if (p["frequency"] - current_cluster[-1]["frequency"]) <= cluster_width_hz:
            current_cluster.append(p)
        else:
            clusters.append(current_cluster)
            current_cluster = [p]
    if current_cluster:
        clusters.append(current_cluster)

    candidates: List[Dict[str, Any]] = []
    for c in clusters:
        # Find dominant peak (carrier) in cluster
        dominant = max(c, key=lambda x: x["power_db"])
        
        # Estimate span if multiple peaks exist in cluster
        if len(c) > 1:
            bw_est = round(c[-1]["frequency"] - c[0]["frequency"], 1)
        else:
            bw_est = None

        snr = dominant["estimated_snr_db"]
        # Heuristic confidence based on SNR: 6dB -> 0.2, 15dB -> 0.6, 25dB+ -> 1.0
        confidence = round(min(1.0, max(0.1, snr / 25.0)), 2)

        candidates.append({
            "frequency": dominant["frequency"],
            "power_db": dominant["power_db"],
            "estimated_snr_db": dominant["estimated_snr_db"],
            "bandwidth_hz": bw_est,
            "confidence": confidence,
        })

    return candidates


def deduplicate_candidates(
    candidates: List[Dict[str, Any]],
    min_distance_hz: float = 6000.0,
) -> List[Dict[str, Any]]:
    """Deduplicate candidates detected across overlapping sweep windows.
    
    When two candidates are within min_distance_hz, preserves the one with
    the higher power / estimated SNR (closest to window center where filter attenuation is lowest).
    
    Args:
        candidates: List of candidates from all windows.
        min_distance_hz: Minimum frequency spacing between distinct RF signals.
    
    Returns:
        Deduplicated list of candidates sorted by frequency.
    """
    if not candidates:
        return []

    sorted_cands = sorted(candidates, key=lambda x: x["frequency"])
    merged: List[Dict[str, Any]] = []

    for c in sorted_cands:
        if not merged:
            merged.append(c)
            continue

        prev = merged[-1]
        if abs(c["frequency"] - prev["frequency"]) < min_distance_hz:
            # Overlapping detection: keep whichever has higher power
            if c["power_db"] > prev["power_db"]:
                merged[-1] = c
        else:
            merged.append(c)

    return merged


def filter_scan_range(
    candidates: List[Dict[str, Any]],
    start_freq: float,
    end_freq: float,
) -> List[Dict[str, Any]]:
    """Strictly filter candidates to within requested [start_frequency, end_frequency]."""
    return [
        c for c in candidates
        if start_freq <= c["frequency"] <= end_freq
    ]
