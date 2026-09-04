"""Unit tests for sdr_mcp.detector."""

import unittest
from sdr_mcp.detector import (
    estimate_noise_floor,
    detect_peaks_in_window,
    cluster_adjacent_peaks,
    deduplicate_candidates,
    filter_scan_range,
)


class TestDetector(unittest.TestCase):

    def test_estimate_noise_floor(self):
        # 10 bins: 8 at -100dB, 2 strong signals at -50dB
        bins = [-100.0] * 8 + [-50.0, -45.0]
        nf = estimate_noise_floor(bins, percentile=0.50)
        self.assertEqual(nf, -100.0)

        # Empty check
        self.assertEqual(estimate_noise_floor([]), -100.0)

    def test_detect_peaks(self):
        # 10 bins with noise floor at -100 dB, carrier at index 5 with -70 dB (SNR = 30 dB)
        bins = [-100.0] * 10
        bins[5] = -70.0  # Peak
        bins[2] = -96.0  # Small bump (SNR = 4 dB, below min_snr=6.0)

        start_freq = 10000000.0  # 10.0 MHz
        step_hz = 1000.0         # 1 kHz per bin
        peaks = detect_peaks_in_window(
            bins=bins,
            start_freq=start_freq,
            step_hz=step_hz,
            noise_floor_db=-100.0,
            min_snr_db=6.0,
        )

        self.assertEqual(len(peaks), 1)
        self.assertEqual(peaks[0]["bin_idx"], 5)
        self.assertEqual(peaks[0]["frequency"], 10005000.0)
        self.assertEqual(peaks[0]["power_db"], -70.0)
        self.assertEqual(peaks[0]["estimated_snr_db"], 30.0)

    def test_threshold_filter(self):
        bins = [-100.0, -90.0, -100.0]
        # Peak at idx 1: -90dB. SNR = 10dB (> 6dB).
        # But if threshold_db = -80dB, should be excluded.
        peaks = detect_peaks_in_window(
            bins=bins,
            start_freq=10000000.0,
            step_hz=1000.0,
            noise_floor_db=-100.0,
            min_snr_db=6.0,
            threshold_db=-80.0,
        )
        self.assertEqual(len(peaks), 0)

    def test_cluster_adjacent_peaks(self):
        # Wideband AM emission: Carrier at 11204 kHz (-40 dB), sidebands at 11201 kHz (-55 dB) and 11207 kHz (-56 dB)
        # Separate station at 11250 kHz (-60 dB)
        peaks = [
            {"frequency": 11201000.0, "power_db": -55.0, "estimated_snr_db": 15.0, "bin_idx": 1},
            {"frequency": 11204000.0, "power_db": -40.0, "estimated_snr_db": 30.0, "bin_idx": 4},
            {"frequency": 11207000.0, "power_db": -56.0, "estimated_snr_db": 14.0, "bin_idx": 7},
            {"frequency": 11250000.0, "power_db": -60.0, "estimated_snr_db": 10.0, "bin_idx": 50},
        ]

        cands = cluster_adjacent_peaks(peaks, cluster_width_hz=8000.0)
        self.assertEqual(len(cands), 2)

        # First candidate should be dominant carrier at 11204000
        self.assertEqual(cands[0]["frequency"], 11204000.0)
        self.assertEqual(cands[0]["power_db"], -40.0)
        self.assertEqual(cands[0]["bandwidth_hz"], 6000.0)

        # Second candidate should be station at 11250000
        self.assertEqual(cands[1]["frequency"], 11250000.0)
        self.assertEqual(cands[1]["power_db"], -60.0)
        self.assertIsNone(cands[1]["bandwidth_hz"])

    def test_deduplicate_candidates(self):
        # Window 1 saw signal at 11204100 (-45 dB)
        # Window 2 saw signal closer to center at 11204000 (-38 dB)
        cands = [
            {"frequency": 11204000.0, "power_db": -38.0, "estimated_snr_db": 22.0, "confidence": 0.88, "bandwidth_hz": None},
            {"frequency": 11204100.0, "power_db": -45.0, "estimated_snr_db": 15.0, "confidence": 0.60, "bandwidth_hz": None},
            {"frequency": 11760000.0, "power_db": -50.0, "estimated_snr_db": 18.0, "confidence": 0.72, "bandwidth_hz": None},
        ]
        deduped = deduplicate_candidates(cands, min_distance_hz=6000.0)
        self.assertEqual(len(deduped), 2)
        # Should keep -38.0 dB version of 11.204 MHz
        self.assertEqual(deduped[0]["frequency"], 11204000.0)
        self.assertEqual(deduped[0]["power_db"], -38.0)

    def test_filter_scan_range(self):
        cands = [
            {"frequency": 9999000.0, "power_db": -50.0},
            {"frequency": 10005000.0, "power_db": -50.0},
            {"frequency": 11000000.0, "power_db": -50.0},
            {"frequency": 11001000.0, "power_db": -50.0},
        ]
        filtered = filter_scan_range(cands, 10000000.0, 11000000.0)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0]["frequency"], 10005000.0)
        self.assertEqual(filtered[1]["frequency"], 11000000.0)


if __name__ == "__main__":
    unittest.main()
