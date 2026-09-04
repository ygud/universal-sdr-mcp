"""Unit tests for sdr_mcp.screener and SDR pre-screening pipeline."""

import unittest
import math
import wave
import struct
import tempfile
import os
from typing import List, Dict, Any

from sdr_mcp.screener import (
    AudioFeature,
    RFFeature,
    extract_audio_features,
    extract_rf_features,
    compute_scores_and_classify,
    rank_and_select_candidates,
)
from sdr_mcp.backend.mock import MockBackend


class TestScreener(unittest.TestCase):

    def _generate_test_wav(
        self,
        duration_sec: float = 1.0,
        signal_type: str = "broadcast",
        sample_rate: int = 48000,
    ) -> str:
        """Helper to create synthetic test WAV files for feature validation."""
        n_samples = int(duration_sec * sample_rate)
        samples = []

        if signal_type == "broadcast":
            # Syllable-modulated tone burst (CV ~0.065, dynamic range > 6dB)
            for i in range(n_samples):
                t = i / sample_rate
                env = 0.55 + 0.35 * math.sin(2.0 * math.pi * 3.2 * t)
                if (int(t * 1.8) % 3) == 0:
                    env *= 0.35
                tone = math.sin(2.0 * math.pi * 440.0 * t) + 0.35 * math.sin(2.0 * math.pi * 880.0 * t)
                val = int(env * 14000.0 * tone + (100.0 * math.sin(100.0 * t)))
                samples.append(max(-32768, min(32767, val)))
        elif signal_type == "carrier_only":
            # Flat low noise floor (CV < 0.03, dynamic range < 2dB)
            for i in range(n_samples):
                val = int(150.0 * math.sin(50.0 * (i / sample_rate)))
                samples.append(val)
        elif signal_type == "noise":
            # Flat pseudo-random noise (CV ~0.025)
            import random
            rng = random.Random(42)
            for _ in range(n_samples):
                val = int(rng.gauss(0, 4000.0))
                samples.append(max(-32768, min(32767, val)))
        elif signal_type == "pause":
            # Low hum during speech pause (CV ~0.035, low dynamic range)
            for i in range(n_samples):
                val = int(250.0 * math.sin(100.0 * (i / sample_rate)))
                samples.append(val)

        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        return path

    def test_audio_feature_extraction(self):
        # 1. Modulated broadcast audio has high CV and dynamic range
        b_path = self._generate_test_wav(1.0, "broadcast")
        try:
            b_feat = extract_audio_features(b_path)
            self.assertGreater(b_feat.envelope_cv, 0.040)
            self.assertGreater(b_feat.dynamic_range_db, 4.0)
            self.assertGreater(b_feat.score, 0.40)
        finally:
            os.remove(b_path)

        # 2. White noise has low CV (< 0.035) and low score
        n_path = self._generate_test_wav(1.0, "noise")
        try:
            n_feat = extract_audio_features(n_path)
            self.assertLess(n_feat.envelope_cv, 0.035)
            self.assertLess(n_feat.score, 0.30)
        finally:
            os.remove(n_path)

    def test_rf_feature_extraction(self):
        # 256 bins across 2.0 MHz: 7812.5 Hz per bin
        # Noise floor at -95 dB, sharp carrier at bin 128 (center) of -65 dB (SNR 30dB)
        bins = [-95.0] * 256
        bins[128] = -65.0
        # Symmetric sidebands at bin 126 and 130
        bins[126] = -78.0
        bins[130] = -78.0

        rf = extract_rf_features(
            bins=bins,
            target_freq=10000000.0,
            start_freq=9000000.0,
            step_hz=7812.5,
        )
        self.assertGreaterEqual(rf.local_snr_db, 20.0)
        self.assertGreaterEqual(rf.prominence_db, 15.0)
        self.assertGreaterEqual(rf.symmetry_score, 0.70)
        self.assertGreaterEqual(rf.score, 0.70)

    def test_strong_broadcast_classification(self):
        # Strong RF and strong Audio modulation -> BROADCAST_ACTIVE
        rf = RFFeature(local_snr_db=22.0, prominence_db=18.0, power_db=-65.0, symmetry_score=0.85, score=0.80)
        aud = AudioFeature(rms=18000.0, envelope_cv=0.065, dynamic_range_db=8.5, zcr_mean=0.05, zcr_std=0.02, score=0.75)
        score, conf, classification, rec = compute_scores_and_classify(rf, aud)

        self.assertEqual(classification, "BROADCAST_ACTIVE")
        self.assertGreaterEqual(score, 0.70)
        self.assertGreaterEqual(conf, 0.80)

    def test_speech_pause_uncertain(self):
        # Strong RF (carrier present) but audio is temporarily quiet (pause) -> UNCERTAIN, NOT NOISE_STATIC
        rf = RFFeature(local_snr_db=24.0, prominence_db=20.0, power_db=-60.0, symmetry_score=0.80, score=0.85)
        aud = AudioFeature(rms=800.0, envelope_cv=0.032, dynamic_range_db=2.5, zcr_mean=0.04, zcr_std=0.008, score=0.25)
        score, conf, classification, rec = compute_scores_and_classify(rf, aud)

        # Crucial recall-first guarantee: must NOT be NOISE_STATIC
        self.assertEqual(classification, "UNCERTAIN")
        self.assertNotEqual(classification, "NOISE_STATIC")

    def test_carrier_only_discrimination(self):
        # Strong carrier with dead silent audio and dynamic range < 3dB -> CARRIER_ONLY
        rf = RFFeature(local_snr_db=25.0, prominence_db=22.0, power_db=-55.0, symmetry_score=0.50, score=0.85)
        aud = AudioFeature(rms=300.0, envelope_cv=0.015, dynamic_range_db=1.0, zcr_mean=0.02, zcr_std=0.002, score=0.10)
        score, conf, classification, rec = compute_scores_and_classify(rf, aud)

        self.assertEqual(classification, "CARRIER_ONLY")

    def test_white_noise_filtering(self):
        # Low RF SNR (< 4dB) and flat white noise audio -> NOISE_STATIC
        rf = RFFeature(local_snr_db=2.0, prominence_db=1.0, power_db=-98.0, symmetry_score=0.30, score=0.10)
        aud = AudioFeature(rms=4000.0, envelope_cv=0.025, dynamic_range_db=1.5, zcr_mean=0.08, zcr_std=0.004, score=0.15)
        score, conf, classification, rec = compute_scores_and_classify(rf, aud)

        self.assertEqual(classification, "NOISE_STATIC")

    def test_pure_rf_ranking(self):
        # Ensure zero semantic database knowledge, purely mathematical SNR + power + 8kHz deduplication
        cands = [
            {"frequency": 11204000.0, "snr_db": 15.0, "power_db": -60.0},
            {"frequency": 11205000.0, "snr_db": 18.0, "power_db": -55.0},  # Close to 11204 (1 kHz away) -> deduplicates!
            {"frequency": 14200000.0, "snr_db": 25.0, "power_db": -50.0},  # Highest SNR
            {"frequency": 7050000.0,  "snr_db": 8.0,  "power_db": -75.0},
        ]
        selected = rank_and_select_candidates(cands, max_probes=2, min_spacing_hz=8000.0)
        self.assertEqual(len(selected), 2)
        # 14.200 MHz must be ranked first (highest SNR)
        self.assertEqual(selected[0]["frequency"], 14200000.0)
        # Second should be 11.205 MHz (higher than 11.204 MHz, which was within 8 kHz)
        self.assertEqual(selected[1]["frequency"], 11205000.0)

    def test_mock_screen_candidates_full_pipeline(self):
        mock = MockBackend(initial_frequency=10000000.0, mode="AM")
        # Configure known synthetic signals: broadcast, carrier_only, weak, noise
        cands = [
            {"frequency": 11204000.0, "estimated_snr_db": 22.0, "power_db": -42.0},  # Broadcast
            {"frequency": 14200000.0, "estimated_snr_db": 20.0, "power_db": -48.0},  # Carrier only
            {"frequency": 11960000.0, "estimated_snr_db": 12.0, "power_db": -68.0},  # Weak
            {"frequency": 8000000.0,  "estimated_snr_db": 2.0,  "power_db": -98.0},  # Pure noise
        ]

        res = mock.screen_candidates(
            candidates=cands,
            max_probes=4,
            probe_duration_sec=0.5,
            min_score_threshold=0.35,
            preserve_uncertain=True,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.probed_count, 4)
        # Noise at 8.0 MHz must be filtered out!
        retained_freqs = [s.frequency for s in res.signals]
        self.assertNotIn(8000000.0, retained_freqs)
        # 11.204 MHz broadcast must be retained and high score
        self.assertIn(11204000.0, retained_freqs)
        b_sig = next(s for s in res.signals if s.frequency == 11204000.0)
        self.assertEqual(b_sig.classification, "BROADCAST_ACTIVE")
        self.assertGreaterEqual(b_sig.broadcast_score, 0.50)

        # Receiver state must be 100% restored to 10.0 MHz AM
        status = mock.get_status()
        self.assertEqual(status.frequency, 10000000.0)
        self.assertEqual(status.mode, "AM")

    def test_tune_failure_isolation(self):
        mock = MockBackend(initial_frequency=10000000.0, mode="AM")
        # Simulate tune failure at 11204000 Hz
        mock.set_tune_failure(11204000.0)

        cands = [
            {"frequency": 11204000.0, "estimated_snr_db": 20.0, "power_db": -50.0},
            {"frequency": 11760000.0, "estimated_snr_db": 18.0, "power_db": -55.0},
        ]
        res = mock.screen_candidates(candidates=cands, max_probes=2, probe_duration_sec=0.5)

        self.assertTrue(res.success)
        # Failed tune frequency was isolated and skipped
        retained_freqs = [s.frequency for s in res.signals]
        self.assertNotIn(11204000.0, retained_freqs)
        # 11.760 MHz was probed successfully
        self.assertIn(11760000.0, retained_freqs)

        # State restored
        self.assertEqual(mock.get_status().frequency, 10000000.0)

    def test_empty_candidates_tolerance(self):
        mock = MockBackend()
        res = mock.screen_candidates(candidates=[], max_probes=10)
        self.assertTrue(res.success)
        self.assertEqual(res.probed_count, 0)
        self.assertEqual(len(res.signals), 0)


if __name__ == "__main__":
    unittest.main()
