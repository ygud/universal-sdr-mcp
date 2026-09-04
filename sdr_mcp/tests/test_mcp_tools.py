"""Test FastMCP tool definitions and execution."""

import unittest
import asyncio
import os
import sdr_mcp.server as srv


class TestMCPTools(unittest.TestCase):

    def setUp(self):
        # Default to mock backend for predictable MCP schema testing
        srv.set_active_backend("mock")

    def test_tool_registration(self):
        tools = [t.name for t in asyncio.run(srv.mcp.list_tools())]
        expected = [
            "sdr_status",
            "sdr_devices",
            "sdr_tune",
            "sdr_set_gain",
            "sdr_set_sample_rate",
            "sdr_get_spectrum",
            "sdr_get_audio",
            "sdr_start_recording",
            "sdr_stop_recording",
            "sdr_switch_backend",
            "sdr_update_analysis",
        ]
        for exp in expected:
            self.assertIn(exp, tools, f"Missing registered tool {exp}")

    def test_update_analysis_tool(self):
        res = srv.sdr_update_analysis(
            country="Test Country",
            language="Test Language",
            station="Test Station",
            program="News",
            confidence=0.95,
            evidence=["Strong carrier", "Distinct interval signal"],
            dialect="Standard"
        )
        self.assertTrue(res["success"])

    def test_status_tool(self):
        st = srv.sdr_status()
        self.assertEqual(st["backend"], "mock")
        self.assertIn("frequency", st)
        self.assertIn("mode", st)
        self.assertIn("audio_ready", st)

    def test_devices_tool(self):
        devs = srv.sdr_devices()
        self.assertEqual(devs["backend"], "mock")
        self.assertIn("available_sources", devs)

    def test_tune_tool(self):
        res = srv.sdr_tune(frequency=15000000.0, mode="AM")
        self.assertTrue(res["success"])
        self.assertEqual(res["frequency"], 15000000.0)

    def test_spectrum_tool(self):
        spec = srv.sdr_get_spectrum(bin_count=64)
        self.assertTrue(spec["available"])
        self.assertEqual(spec["bin_count"], 64)
        self.assertEqual(len(spec["bins"]), 64)

    def test_audio_tool(self):
        audio = srv.sdr_get_audio(duration_sec=1.0)
        self.assertTrue(audio["success"])
        self.assertEqual(audio["sample_rate"], 48000)
        self.assertGreater(audio["samples_recorded"], 0)

    def test_recording_tools(self):
        rec_start = srv.sdr_start_recording()
        self.assertEqual(rec_start["status"], "started")
        rec_stop = srv.sdr_stop_recording()
        self.assertEqual(rec_stop["status"], "stopped")

    def test_backend_switch(self):
        # Switch to sdrpp
        sw = srv.sdr_switch_backend("sdrpp")
        self.assertEqual(sw["status"], "ok")
        self.assertEqual(srv.get_active_backend().name, "sdrpp")

        # Switch back to mock
        sw2 = srv.sdr_switch_backend("mock")
        self.assertEqual(sw2["status"], "ok")
        self.assertEqual(srv.get_active_backend().name, "mock")


if __name__ == "__main__":
    unittest.main()
