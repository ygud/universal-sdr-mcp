"""Candidate Signal Pre-screener for Universal SDR MCP (v0.1)

Algorithmic pre-screening between RF sweep (sdr_scan) and LLM investigation:
- Decoupled RF and Audio evidence extraction
- Zero-dependency mathematical signal analysis (pure Python stdlib: math, wave, struct)
- Four-state classification: BROADCAST_ACTIVE, UNCERTAIN, CARRIER_ONLY, NOISE_STATIC
- Recall-first filtering policy (preserves weak signals and speech pauses)
- Pure RF Candidate Prior Ranking (zero broadcast semantic priors)
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import math
import wave
import struct


@dataclass
class AudioFeature:
    rms: float
    envelope_cv: float
    dynamic_range_db: float
    zcr_mean: float
    zcr_std: float
    score: float  # Normalized S_Audio [0.0, 1.0]


@dataclass
class RFFeature:
    local_snr_db: float
    prominence_db: float
    power_db: float
    symmetry_score: float
    score: float  # Normalized S_RF [0.0, 1.0]


def extract_audio_features(
    wav_path: str,
    frame_ms: float = 50.0,
) -> AudioFeature:
    """Extract acoustic modulation features from a demodulated audio WAV file.
    
    Args:
        wav_path: Path to mono 16-bit PCM WAV file.
        frame_ms: Analysis frame size in milliseconds (default 50ms).
        
    Returns:
        AudioFeature object.
    """
    try:
        with wave.open(wav_path, "rb") as w:
            sr = w.getframerate()
            n_frames = w.getnframes()
            if n_frames == 0 or sr == 0:
                return AudioFeature(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            raw = w.readframes(n_frames)
            samples = struct.unpack(f"<{len(raw)//2}h", raw)
    except Exception:
        return AudioFeature(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    if not samples:
        return AudioFeature(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    # 1. Overall RMS
    sum_sq = sum(s * s for s in samples)
    overall_rms = math.sqrt(sum_sq / len(samples))

    # 2. Slice into frame_ms slices
    frame_len = max(1, int(sr * (frame_ms / 1000.0)))
    frame_rms_list: List[float] = []
    frame_zcr_list: List[float] = []

    for i in range(0, len(samples) - frame_len + 1, frame_len):
        chunk = samples[i : i + frame_len]
        # RMS of chunk
        f_sq = sum(s * s for s in chunk)
        f_rms = math.sqrt(f_sq / frame_len)
        frame_rms_list.append(f_rms)

        # Zero crossing rate of chunk
        zc = sum(
            1
            for j in range(1, frame_len)
            if (chunk[j - 1] >= 0 and chunk[j] < 0)
            or (chunk[j - 1] < 0 and chunk[j] >= 0)
        )
        frame_zcr_list.append(zc / frame_len)

    if not frame_rms_list:
        return AudioFeature(round(overall_rms, 1), 0.0, 0.0, 0.0, 0.0, 0.0)

    # 3. Envelope CV = std(E) / (mean(E) + eps)
    mean_e = sum(frame_rms_list) / len(frame_rms_list)
    if mean_e > 0:
        var_e = sum((e - mean_e) ** 2 for e in frame_rms_list) / len(frame_rms_list)
        std_e = math.sqrt(var_e)
        cv = std_e / mean_e
    else:
        cv = 0.0

    # 4. Decibel dynamic range: p90 vs p10
    sorted_e = sorted(frame_rms_list)
    idx_p10 = max(0, int(len(sorted_e) * 0.10))
    idx_p90 = min(len(sorted_e) - 1, int(len(sorted_e) * 0.90))
    e10 = sorted_e[idx_p10]
    e90 = sorted_e[idx_p90]
    if e10 > 0 and e90 > 0:
        dyn_db = 20.0 * math.log10((e90 + 1.0) / (e10 + 1.0))
    else:
        dyn_db = 0.0

    # 5. ZCR mean and std
    mean_zcr = sum(frame_zcr_list) / len(frame_zcr_list)
    var_zcr = sum((z - mean_zcr) ** 2 for z in frame_zcr_list) / len(frame_zcr_list)
    std_zcr = math.sqrt(var_zcr)

    # 6. Normalized S_Audio calculation
    # - CV: Gaussian noise ~0.025, speech/music >= 0.045
    s_cv = min(1.0, max(0.0, (cv - 0.028) / 0.045))
    # - Dynamic range: flat noise < 2dB, speech > 5dB
    s_dyn = min(1.0, max(0.0, (dyn_db - 2.0) / 10.0))
    # - ZCR std: speech transitions have high variance
    s_zcr = min(1.0, max(0.0, (std_zcr - 0.005) / 0.020))

    s_audio = 0.45 * s_cv + 0.35 * s_dyn + 0.20 * s_zcr

    return AudioFeature(
        rms=round(overall_rms, 1),
        envelope_cv=round(cv, 4),
        dynamic_range_db=round(dyn_db, 2),
        zcr_mean=round(mean_zcr, 4),
        zcr_std=round(std_zcr, 4),
        score=round(s_audio, 3),
    )


def extract_rf_features(
    bins: List[float],
    target_freq: float,
    start_freq: float,
    step_hz: float,
) -> RFFeature:
    """Extract local RF features and sideband symmetry around target frequency.
    
    Args:
        bins: FFT power values across passband in dB.
        target_freq: Tuned frequency of interest in Hz.
        start_freq: Frequency of bin 0 in Hz.
        step_hz: Frequency span per FFT bin in Hz.
        
    Returns:
        RFFeature object.
    """
    if not bins or step_hz <= 0:
        return RFFeature(0.0, 0.0, -100.0, 0.5, 0.0)

    n_bins = len(bins)
    target_bin = int(round((target_freq - start_freq) / step_hz))
    target_bin = max(0, min(n_bins - 1, target_bin))

    # 1. Local window definition (+/- 25 kHz)
    span_bins = max(3, int(round(25000.0 / step_hz)))
    w_start = max(0, target_bin - span_bins)
    w_end = min(n_bins, target_bin + span_bins + 1)
    local_bins = bins[w_start:w_end]

    # Local noise floor: median (50th percentile) of local window
    sorted_local = sorted(local_bins)
    local_nf = sorted_local[len(sorted_local) // 2]

    # Peak in narrow carrier zone (+/- 4 kHz)
    carrier_bins = max(1, int(round(4000.0 / step_hz)))
    c_start = max(0, target_bin - carrier_bins)
    c_end = min(n_bins, target_bin + carrier_bins + 1)
    peak_pwr = max(bins[c_start:c_end])
    local_snr = max(0.0, peak_pwr - local_nf)

    # Prominence: peak minus shoulders (+/- 8 to 15 kHz)
    sh_bins_in = max(1, int(round(8000.0 / step_hz)))
    sh_bins_out = max(2, int(round(15000.0 / step_hz)))
    left_sh = bins[max(0, target_bin - sh_bins_out) : max(0, target_bin - sh_bins_in)]
    right_sh = bins[min(n_bins, target_bin + sh_bins_in) : min(n_bins, target_bin + sh_bins_out)]
    shoulders = left_sh + right_sh
    if shoulders:
        sh_mean = sum(shoulders) / len(shoulders)
        prominence = max(0.0, peak_pwr - sh_mean)
    else:
        prominence = local_snr * 0.7

    # Concentration: ratio of carrier energy to local window energy
    c_pwr_lin = sum(10.0 ** (p / 10.0) for p in bins[c_start:c_end])
    w_pwr_lin = sum(10.0 ** (p / 10.0) for p in local_bins)
    conc_ratio = (c_pwr_lin / w_pwr_lin) if w_pwr_lin > 0 else 0.0

    # Symmetry: lower sideband [-15kHz, -1kHz] vs upper sideband [+1kHz, +15kHz]
    sb_bins = max(2, int(round(15000.0 / step_hz)))
    lsb_bins = bins[max(0, target_bin - sb_bins) : max(0, target_bin)]
    usb_bins = bins[min(n_bins, target_bin + 1) : min(n_bins, target_bin + sb_bins + 1)]
    if lsb_bins and usb_bins:
        p_lsb = sum(10.0 ** (p / 10.0) for p in lsb_bins)
        p_usb = sum(10.0 ** (p / 10.0) for p in usb_bins)
        delta_db = abs(10.0 * math.log10(max(1e-12, p_lsb) / max(1e-12, p_usb)))
        symmetry_score = min(1.0, max(0.0, 1.0 - (delta_db / 12.0)))
    else:
        symmetry_score = 0.5

    # Normalized S_RF calculation
    s_snr = min(1.0, max(0.0, (local_snr - 4.0) / 20.0))
    s_prom = min(1.0, max(0.0, (prominence - 3.0) / 15.0))
    s_conc = min(1.0, max(0.0, (conc_ratio - 0.20) / 0.50))

    s_rf = 0.50 * s_snr + 0.35 * s_prom + 0.15 * s_conc

    return RFFeature(
        local_snr_db=round(local_snr, 1),
        prominence_db=round(prominence, 1),
        power_db=round(peak_pwr, 1),
        symmetry_score=round(symmetry_score, 3),
        score=round(s_rf, 3),
    )


def compute_scores_and_classify(
    rf: RFFeature,
    audio: AudioFeature,
) -> Tuple[float, float, str, str]:
    """Fuse RF and Audio evidence into BroadcastScore, confidence, and four-state class.
    
    Formula:
        BroadcastScore = 0.45 * S_RF + 0.45 * S_Audio + 0.10 * S_Spec
        (Sum of weights = 1.00; S_Temp has weight 0.0 in v0.1)
        
    Returns:
        (broadcast_score, confidence, classification, recommendation)
    """
    # Normalized weights sum strictly to 1.00
    b_score = (
        0.45 * rf.score
        + 0.45 * audio.score
        + 0.10 * rf.symmetry_score
    )
    b_score = min(1.0, max(0.0, b_score))

    # Confidence calculation: penalized if clipping or near noise floor
    is_clipping = 1.0 if audio.rms > 32000.0 else 0.0
    is_marginal_snr = 1.0 if rf.local_snr_db < 6.0 else 0.0
    confidence = min(1.0, max(0.2, 1.0 - (0.4 * is_clipping) - (0.3 * is_marginal_snr)))

    # Four-State Decision Matrix (Recall-First Policy)
    if rf.score >= 0.45 and audio.score >= 0.40:
        classification = "BROADCAST_ACTIVE"
        rec = "Active broadcast modulation detected. Recommended for audio transcription and identification."
    elif rf.score >= 0.50 and audio.score < 0.20 and audio.dynamic_range_db < 3.0:
        classification = "CARRIER_ONLY"
        rec = "Strong RF carrier present, but audio is flat with no modulation. Likely carrier standby or spur."
    elif rf.score < 0.25 and audio.score < 0.25:
        classification = "NOISE_STATIC"
        rec = "Signal matches background noise floor with no significant RF or audio modulation."
    else:
        # All ambiguous cases fall into UNCERTAIN (speech pause, deep fading, weak station)
        classification = "UNCERTAIN"
        rec = "Signal present with ambiguous audio dynamics (possible speech pause or channel fading). Keep for investigation."

    return (
        round(b_score, 3),
        round(confidence, 2),
        classification,
        rec,
    )


def rank_and_select_candidates(
    candidates: List[Dict[str, Any]],
    max_probes: int = 12,
    min_spacing_hz: float = 8000.0,
) -> List[Dict[str, Any]]:
    """Prioritize raw RF scan candidates using pure mathematical RF metrics.
    
    Strictly free of any broadcast frequency table or semantic database knowledge.
    
    Args:
        candidates: Raw candidate list from sdr_scan.
        max_probes: Maximum candidates to probe in probe loop.
        min_spacing_hz: Minimum frequency spacing between probed candidates.
        
    Returns:
        Selected top candidates.
    """
    if not candidates or max_probes <= 0:
        return []

    # 1. Compute pure RF prior score: 70% SNR + 30% Power
    scored = []
    for c in candidates:
        freq = float(c.get("frequency", 0.0))
        snr = float(c.get("estimated_snr_db", c.get("snr_db", 0.0)))
        pwr = float(c.get("power_db", -100.0))

        s_snr = min(1.0, max(0.0, snr / 30.0))
        s_pwr = min(1.0, max(0.0, (pwr - (-100.0)) / 50.0))
        prior_score = 0.70 * s_snr + 0.30 * s_pwr

        item = dict(c)
        item["_prior_score"] = prior_score
        item["frequency"] = freq
        scored.append(item)

    # 2. Sort descending by prior score
    scored.sort(key=lambda x: x["_prior_score"], reverse=True)

    # 3. Spatial deduplication to avoid probing multiple bins of same carrier
    selected: List[Dict[str, Any]] = []
    for c in scored:
        freq = c["frequency"]
        if any(abs(freq - s["frequency"]) < min_spacing_hz for s in selected):
            continue
        selected.append(c)
        if len(selected) >= max_probes:
            break

    return selected
