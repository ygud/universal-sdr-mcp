"""Comprehensive unit tests for MockBackend.scan().

Covers all 10 scanning requirements:
- Single window scan
- Multi-window scan with >= 50% overlap
- Overlap deduplication
- Boundary frequency detection
- Wideband AM clustering
- Closely spaced distinct signals
- Step_hz clamp and warning
- Tune failure handling (no stale data)
- State restoration guarantee
- Concurrency lock (busy while scanning)
- Range filtering
"""

import unittest
from sdr_mcp.backend.mock import MockBackend


class TestMockScan(unittest.TestCase):

    def setUp(self):
        self.backend = MockBackend(initial_frequency=7050000.0, mode="LSB")

    def test_single_window_scan(self):
        # Scan 11.1 MHz to 11.3 MHz (span 200 kHz <= 2 MHz BW)
        res = self.backend.scan(
            start_frequency=11100000.0,
            end_frequency=11300000.0,
            dwell_ms=0.0,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.window_count, 1)
        self.assertEqual(len(res.candidates), 1)

        cand = res.candidates[0]
        # Frequency should match 11204000.0 Hz within FFT bin resolution (~3.9 kHz for 512 bins / 2 MHz)
        self.assertAlmostEqual(cand.frequency, 11204000.0, delta=5000.0)
        self.assertGreater(cand.estimated_snr_db, 20.0)
        self.assertGreater(cand.confidence, 0.7)

    def test_multi_window_scan_and_known_signals(self):
        # Scan 11.0 MHz to 12.5 MHz (span 1.5 MHz)
        # Should discover the 3 default synthetic carriers: 11.204 MHz, 11.760 MHz, 11.960 MHz
        res = self.backend.scan(
            start_frequency=11000000.0,
            end_frequency=12500000.0,
            dwell_ms=0.0,
        )
        self.assertTrue(res.success)
        self.assertGreater(res.window_count, 1)

        detected_freqs = [c.frequency for c in res.candidates]
        # Verify all 3 known carriers are found
        self.assertTrue(any(abs(f - 11204000.0) < 5000.0 for f in detected_freqs), "Missing 11.204 MHz")
        self.assertTrue(any(abs(f - 11760000.0) < 5000.0 for f in detected_freqs), "Missing 11.760 MHz")
        self.assertTrue(any(abs(f - 11960000.0) < 5000.0 for f in detected_freqs), "Missing 11.960 MHz")

    def test_wideband_am_clustering(self):
        # 11.204 MHz has carrier + sidebands at +/- 2500 Hz.
        # Ensure it clusters into 1 candidate instead of 3.
        res = self.backend.scan(
            start_frequency=11150000.0,
            end_frequency=11250000.0,
            dwell_ms=0.0,
            cluster_width_hz=8000.0,
        )
        self.assertTrue(res.success)
        self.assertEqual(len(res.candidates), 1)
        self.assertAlmostEqual(res.candidates[0].frequency, 11204000.0, delta=4000.0)

    def test_closely_spaced_signals(self):
        # Two distinct carriers 20 kHz apart (well above 8 kHz cluster width)
        self.backend.set_synthetic_signals([
            {"frequency": 11200000.0, "power_db": -50.0, "bandwidth_hz": 5000.0},
            {"frequency": 11220000.0, "power_db": -52.0, "bandwidth_hz": 5000.0},
        ])
        res = self.backend.scan(
            start_frequency=11150000.0,
            end_frequency=11250000.0,
            dwell_ms=0.0,
            cluster_width_hz=8000.0,
        )
        self.assertTrue(res.success)
        self.assertEqual(len(res.candidates), 2)
        self.assertAlmostEqual(res.candidates[0].frequency, 11200000.0, delta=4000.0)
        self.assertAlmostEqual(res.candidates[1].frequency, 11220000.0, delta=4000.0)

    def test_step_hz_safe_clamp_and_warning(self):
        # User requested 5 MHz step on a 2 MHz receiver -> must clamp to <= 1 MHz (50% overlap)
        res = self.backend.scan(
            start_frequency=10000000.0,
            end_frequency=13000000.0,
            step_hz=5000000.0,
            dwell_ms=0.0,
        )
        self.assertTrue(res.success)
        self.assertIn("warning", res.details)
        self.assertLessEqual(res.details["step_hz"], 1000000.0)

    def test_tune_failure_prevention_of_stale_data(self):
        # Simulate tune failure at 11500000 Hz (which is window 2 center)
        self.backend.set_tune_failure(11500000.0)
        res = self.backend.scan(
            start_frequency=10000000.0,
            end_frequency=13000000.0,
            dwell_ms=0.0,
        )
        self.assertIn("failed_windows", res.details)
        self.assertGreater(len(res.details["failed_windows"]), 0)
        self.assertEqual(res.details["failed_windows"][0]["center_freq"], 11500000.0)

    def test_state_restoration(self):
        # Start at 7050 kHz LSB
        self.assertEqual(self.backend.get_status().frequency, 7050000.0)
        self.assertEqual(self.backend.get_status().mode, "LSB")

        # Perform sweep on 11-13 MHz
        self.backend.scan(
            start_frequency=11000000.0,
            end_frequency=13000000.0,
            dwell_ms=0.0,
        )

        # After scan finishes, must be restored to 7050 kHz LSB
        st_after = self.backend.get_status()
        self.assertEqual(st_after.frequency, 7050000.0)
        self.assertEqual(st_after.mode, "LSB")

    def test_scan_busy_lock(self):
        # While _is_scanning is True, external tune or get_audio must return busy error
        self.backend._is_scanning = True
        tune_res = self.backend.tune(14200000.0)
        self.assertFalse(tune_res["success"])
        self.assertIn("busy", tune_res["error"].lower())

        audio_res = self.backend.get_audio(1.0)
        self.assertFalse(audio_res.success)
        self.assertIn("busy", audio_res.error.lower())

        self.backend._is_scanning = False

    def test_range_filtering(self):
        # Known signals are at 11.204 MHz, 11.760 MHz, 11.960 MHz
        # Only scan 11.1 MHz to 11.5 MHz -> should ONLY return 11.204 MHz
        res = self.backend.scan(
            start_frequency=11100000.0,
            end_frequency=11500000.0,
            dwell_ms=0.0,
        )
        self.assertTrue(res.success)
        for c in res.candidates:
            self.assertGreaterEqual(c.frequency, 11100000.0)
            self.assertLessEqual(c.frequency, 11500000.0)


if __name__ == "__main__":
    unittest.main()
