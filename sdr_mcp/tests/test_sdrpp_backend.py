"""Test SdrppBackend against live SDR++ plugin."""

import unittest
import os
import time
from sdr_mcp.backend.sdrpp import SdrppBackend


class TestSdrppBackend(unittest.TestCase):

    def setUp(self):
        self.backend = SdrppBackend()
        if not self.backend.is_connected():
            self.skipTest("SDR++ is not running with sdrpp_agent plugin loaded on 127.0.0.1:19870")

    def test_status(self):
        st = self.backend.get_status()
        self.assertEqual(st.backend, "sdrpp")
        self.assertTrue(st.connected)
        self.assertGreater(st.frequency, 0.0)
        self.assertIn(st.mode, ["AM", "WFM", "NFM", "USB", "LSB", "CW", "DSB", "RAW"])
        self.assertTrue(st.audio_ready)

    def test_devices(self):
        devs = self.backend.get_devices()
        self.assertEqual(devs["backend"], "sdrpp")
        self.assertTrue(devs["connected"])
        self.assertIn("RTL-SDR", devs["available_sources"])

    def test_tune(self):
        target_freq = 11576000.0
        res = self.backend.tune(target_freq, "AM")
        self.assertTrue(res["success"])
        self.assertEqual(res["frequency"], target_freq)
        self.assertEqual(res["mode"], "AM")

        st = self.backend.get_status()
        self.assertEqual(st.frequency, target_freq)
        self.assertEqual(st.mode, "AM")

    def test_set_gain_honesty(self):
        # SDR++ source modules don't provide programmatic gain interface
        res = self.backend.set_gain(28.0)
        self.assertFalse(res.get("supported", True))
        self.assertEqual(res.get("status"), "NOT_SUPPORTED")

    def test_spectrum(self):
        spec = self.backend.get_spectrum(bin_count=256)
        self.assertTrue(spec.available)
        self.assertEqual(spec.backend, "sdrpp")
        self.assertFalse(spec.simulated)
        self.assertGreater(spec.bandwidth, 0.0)
        self.assertGreater(spec.bin_count, 0)
        self.assertEqual(len(spec.bins), spec.bin_count)

    def test_audio(self):
        audio = self.backend.get_audio(duration_sec=2.0)
        self.assertTrue(audio.success)
        self.assertFalse(audio.simulated)
        self.assertEqual(audio.sample_rate, 48000)
        self.assertEqual(audio.samples_recorded, int(2.0 * 48000))
        self.assertTrue(os.path.exists(audio.path))
        self.assertGreaterEqual(audio.peak, 0)

    def test_recording(self):
        rec_path = "/tmp/test_real_sdrpp_rec.wav"
        if os.path.exists(rec_path):
            os.remove(rec_path)

        rec_start = self.backend.start_recording(rec_path)
        self.assertEqual(rec_start.status, "started")
        time.sleep(1.0)
        rec_stop = self.backend.stop_recording()
        self.assertEqual(rec_stop.status, "stopped")
        self.assertEqual(rec_stop.path, rec_path)
        self.assertTrue(os.path.exists(rec_path))
        self.assertGreater(rec_stop.duration_sec, 0.5)
        self.assertGreater(rec_stop.samples_recorded, 20000)
        self.assertGreater(rec_stop.size_bytes, 40000)

    def test_update_analysis(self):
        res = self.backend.update_analysis(
            country="China",
            language="Mandarin",
            station="CNR 1",
            program="National News",
            confidence=0.91,
            evidence=["Station ID jingle heard", "Standard Mandarin news format"],
            dialect="Beijing Mandarin"
        )
        self.assertTrue(res.get("success", False))


if __name__ == "__main__":
    unittest.main()
