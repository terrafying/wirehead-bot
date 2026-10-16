import unittest
from unittest import mock

import main


class HolderTests(unittest.TestCase):
    def test_lookup_caches_and_parses(self):
        calls = []
        class Resp:
            def read(self): return b'{"tier": "PATRON"}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return Resp()
        with mock.patch.object(main.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(main.holder_tier("SomeUser"), "PATRON")
            self.assertEqual(main.holder_tier("SomeUser"), "PATRON")   # cached
        self.assertEqual(len(calls), 1)
        self.assertIn("/holder/someuser", calls[0])
        main._holder_cache.clear()

    def test_lookup_failure_is_unrated(self):
        with mock.patch.object(main.urllib.request, "urlopen",
                               side_effect=OSError("down")):
            self.assertEqual(main.holder_tier("gone"), "UNRATED")
        main._holder_cache.clear()

    def test_holder_lane_forces_deluded(self):
        with mock.patch.object(main, "DELUDED_ENDPOINT", "ep27"), \
             mock.patch.object(main, "ENDPOINT", "ep8"), \
             mock.patch.object(main, "PERSONA_REGISTER", {"ep27": "register line"}), \
             mock.patch.object(main, "_run_ep", return_value="voice reply") as run, \
             mock.patch.object(main, "deluded_lane") as dl, \
             mock.patch.object(main, "sim_lane") as sl:
            out = main.run_job("hello", "sadness", 5, holder="PATRON")
            dl.assert_not_called()                         # no roll: holders always
            sl.assert_not_called()
        self.assertEqual(out, "voice reply")
        self.assertEqual(run.call_args[0][0], "ep27")   # the 27B voice lane

    def test_non_holder_rolls_lanes_as_before(self):
        with mock.patch.object(main, "DELUDED_ENDPOINT", "ep27"), \
             mock.patch.object(main, "ENDPOINT", "ep8"), \
             mock.patch.object(main, "_run_ep", return_value="base reply"), \
             mock.patch.object(main, "deluded_lane", return_value=(False, "")), \
             mock.patch.object(main, "sim_lane", return_value=(False, "")):
            out = main.run_job("hello", "sadness", 5, holder="UNRATED")
        self.assertEqual(out, "base reply")


if __name__ == "__main__":
    unittest.main()