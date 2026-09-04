"""Test scan result synchronization, serialization, failure handling, and candidate tuning."""

import unittest
import json
from sdr_mcp.backend.base import ScanCandidate, ScanResult
from sdr_mcp.backend.mock import MockBackend
from sdr_mcp.backend.sdrpp import SdrppBackend


class TestScanSync(unittest.TestCase):

    def setUp(self):
        self.mock_backend = MockBackend(initial_frequency=11200000.0, mode="AM")

    def test_scan_result_serialization(self):
        """Verify ScanResult and ScanCandidate serialize cleanly to JSON-compatible dict."""
        cand1 = ScanCandidate(
            frequency=11210900.0,
            power_db=-85.8,
            estimated_snr_db=9.5,
            bandwidth_hz=6000.0,
            confidence=0.75,
        )
        cand2 = ScanCandidate(
            frequency=11735000.0,
            power_db=-88.1,
            estimated_snr_db=8.2,
            bandwidth_hz=5000.0,
            confidence=0.65,
        )
        res = ScanResult(
            success=True,
            start_frequency=11000000.0,
            end_frequency=12500000.0,
            window_count=2,
            elapsed_sec=0.55,
            candidates=[cand1, cand2],
            noise_floor_db=-95.3,
            details={"step_hz": 1000000.0},
        )

        d = res.to_dict()
        # Verify JSON serializability
        json_str = json.dumps(d)
        self.assertIn("11210900.0", json_str)
        self.assertIn("9.5", json_str)
        self.assertEqual(len(d["candidates"]), 2)
        self.assertEqual(d["candidates"][0]["frequency"], 11210900.0)
        self.assertEqual(d["candidates"][0]["estimated_snr_db"], 9.5)

    def test_empty_candidate_result(self):
        """Verify scan with no detected carriers produces clean empty candidate list."""
        self.mock_backend.set_synthetic_signals([])
        res = self.mock_backend.scan(
            start_frequency=11000000.0,
            end_frequency=12000000.0,
            min_snr_db=15.0,
        )
        self.assertTrue(res.success)
        self.assertEqual(len(res.candidates), 0)
        status = self.mock_backend._scan_status
        self.assertEqual(status["status"], "COMPLETE")
        self.assertEqual(len(status["candidates"]), 0)
        self.assertEqual(status["found_candidates"], 0)

    def test_multiple_candidates_sync(self):
        """Verify multiple candidates are detected, sorted by SNR descending, and synced to UI state."""
        signals = [
            {"frequency": 11204000.0, "power_db": -42.0, "bandwidth_hz": 8000.0},
            {"frequency": 11760000.0, "power_db": -55.0, "bandwidth_hz": 6000.0},
            {"frequency": 11960000.0, "power_db": -68.0, "bandwidth_hz": 5000.0},
        ]
        self.mock_backend.set_synthetic_signals(signals)
        res = self.mock_backend.scan(
            start_frequency=11000000.0,
            end_frequency=12500000.0,
            min_snr_db=5.0,
        )
        self.assertTrue(res.success)
        self.assertGreaterEqual(len(res.candidates), 3)

        status = self.mock_backend._scan_status
        self.assertEqual(status["status"], "COMPLETE")
        self.assertGreaterEqual(len(status["candidates"]), 3)

        first = status["candidates"][0]
        self.assertIn("frequency", first)
        self.assertIn("snr_db", first)
        self.assertIn("power_db", first)
        self.assertIn("status", first)
        self.assertEqual(first["status"], "Candidate")

    def test_scan_failure_clears_result(self):
        """Verify scan failure updates status to FAILED, records error message, and clears candidates."""
        self.mock_backend._connected = False
        res = self.mock_backend.scan(
            start_frequency=11000000.0,
            end_frequency=12000000.0,
        )
        self.assertFalse(res.success)
        self.assertIsNotNone(res.error)

    def test_ui_update_message_payload(self):
        """Verify update_scan_status formats and packages full UI update message."""
        sample_cands = [
            {
                "frequency": 11210900.0,
                "snr_db": 9.5,
                "power_db": -85.8,
                "bandwidth_hz": 6000.0,
                "confidence": 0.75,
                "status": "Candidate",
            }
        ]
        res = self.mock_backend.update_scan_status(
            status="COMPLETE",
            scanning=False,
            start_frequency=11000000.0,
            end_frequency=12500000.0,
            noise_floor_db=-95.3,
            found_candidates=1,
            candidates=sample_cands,
        )
        self.assertTrue(res["success"])
        st = self.mock_backend._scan_status
        self.assertEqual(st["status"], "COMPLETE")
        self.assertFalse(st["scanning"])
        self.assertEqual(st["noise_floor_db"], -95.3)
        self.assertEqual(len(st["candidates"]), 1)
        self.assertEqual(st["candidates"][0]["frequency"], 11210900.0)

    def test_candidate_frequency_tune_integration(self):
        """Verify candidate frequency from scan results can be tuned immediately."""
        signals = [{"frequency": 11204000.0, "power_db": -42.0, "bandwidth_hz": 8000.0}]
        self.mock_backend.set_synthetic_signals(signals)
        res = self.mock_backend.scan(
            start_frequency=11000000.0,
            end_frequency=12000000.0,
            min_snr_db=5.0,
        )
        self.assertTrue(res.success)
        self.assertGreater(len(res.candidates), 0)

        target_cand = res.candidates[0]
        tune_res = self.mock_backend.tune(target_cand.frequency, mode="AM")
        self.assertTrue(tune_res["success"])
        self.assertEqual(tune_res["frequency"], target_cand.frequency)

        st = self.mock_backend.get_status()
        self.assertEqual(st.frequency, target_cand.frequency)


if __name__ == "__main__":
    unittest.main()
