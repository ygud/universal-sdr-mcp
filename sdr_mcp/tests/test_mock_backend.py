"""Test Mock Backend in isolation."""

import unittest
import os
import time
from sdr_mcp.backend.mock import MockBackend


class TestMockBackend(unittest.TestCase):

    def setUp(self):
        self.backend = MockBackend(initial_frequency=14200000.0, mode="USB")

    def test_status(self):
        st = self.backend.get_status()
        self.assertEqual(st.backend, "mock")
        self.assertTrue(st.connected)
        self.assertEqual(st.frequency, 14200000.0)
        self.assertEqual(st.mode, "USB")
        self.assertTrue(st.details.get("simulated"))

    def test_devices(self):
        devs = self.backend.get_devices()
        self.assertEqual(devs["backend"], "mock")
        self.assertTrue(devs["simulated"])
        self.assertGreaterEqual(devs["count"], 1)

    def test_tune(self):
        res = self.backend.tune(7050000.0, "LSB")
        self.assertTrue(res["success"])
        self.assertEqual(res["frequency"], 7050000.0)
        self.assertEqual(res["mode"], "LSB")
        self.assertTrue(res["simulated"])

        st = self.backend.get_status()
        self.assertEqual(st.frequency, 7050000.0)
        self.assertEqual(st.mode, "LSB")

    def test_spectrum(self):
        spec = self.backend.get_spectrum(bin_count=128)
        self.assertTrue(spec.available)
        self.assertTrue(spec.simulated)
        self.assertEqual(spec.backend, "mock")
        self.assertEqual(spec.bin_count, 128)
        self.assertEqual(len(spec.bins), 128)
        self.assertGreater(spec.peak_db, -60.0)

    def test_audio(self):
        audio = self.backend.get_audio(duration_sec=1.0)
        self.assertTrue(audio.success)
        self.assertTrue(audio.simulated)
        self.assertEqual(audio.sample_rate, 48000)
        self.assertEqual(audio.samples_recorded, int(1.0 * 48000))
        self.assertTrue(os.path.exists(audio.path))
        self.assertGreater(audio.rms, 0.0)
        self.assertGreater(audio.peak, 0)

    def test_recording(self):
        rec_start = self.backend.start_recording()
        self.assertEqual(rec_start.status, "started")
        time.sleep(0.05)
        rec_stop = self.backend.stop_recording()
        self.assertEqual(rec_stop.status, "stopped")
        self.assertTrue(os.path.exists(rec_stop.path))
        self.assertGreaterEqual(rec_stop.size_bytes, 44)

    def test_disconnect_simulation(self):
        self.backend.set_connected(False)
        st = self.backend.get_status()
        self.assertFalse(st.connected)
        spec = self.backend.get_spectrum()
        self.assertFalse(spec.available)
        audio = self.backend.get_audio(1.0)
        self.assertFalse(audio.success)

    def test_update_analysis(self):
        res = self.backend.update_analysis(
            country="Japan",
            language="Japanese",
            station="NHK World",
            program="Radio Japan News",
            confidence=0.88,
            evidence=["Time signal chime heard", "Japanese identification"],
            dialect="Tokyo Standard"
        )
        self.assertTrue(res.get("success", False))
        self.assertTrue(res.get("simulated", False))


if __name__ == "__main__":
    unittest.main()
