import unittest
from unittest import mock

import main

STUCK = {"jobs": {"inQueue": 80}, "workers": {"running": 0, "initializing": 0, "unhealthy": 1}}
OK = {"jobs": {"inQueue": 0}, "workers": {"running": 1}}


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        main._lane.clear()
        self.p = [mock.patch.object(main, "ENDPOINT", "base"), mock.patch.object(main, "DELUDED_ENDPOINT", "voice"),
                  mock.patch.object(main, "SIM_ENDPOINT", ""), mock.patch.object(main, "FALLBACK_ENDPOINT", "fb"),
                  mock.patch.object(main, "AUTOFIX", set())]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()
        main._lane.clear()

    def health(self, table):
        return lambda method, url, body=None: table[url.split("/v2/")[1].split("/")[0]]

    def test_stuck_lane_marked_down_after_grace_then_restored(self):
        t = {"base": OK, "voice": STUCK, "fb": OK}
        with mock.patch.object(main, "_rp", self.health(t)):
            main.watch_once(now=1000)
            self.assertFalse(main.lane_down("voice"))          # inside the grace period
            main.watch_once(now=1000 + main.STUCK_SECS + 1)
            self.assertTrue(main.lane_down("voice"))
            t["voice"] = OK
            main.watch_once(now=5000)
            self.assertFalse(main.lane_down("voice"))

    def test_autofix_only_listed_lanes_and_rate_limited(self):
        t = {"base": STUCK, "voice": STUCK, "fb": OK}
        with mock.patch.object(main, "_rp", self.health(t)), mock.patch.object(main, "AUTOFIX", {"voice"}), \
             mock.patch.object(main, "_recycle") as rec:
            main.watch_once(now=1000); main.watch_once(now=1000 + main.STUCK_SECS + 1)
            main.watch_once(now=1000 + main.STUCK_SECS + 60)
        self.assertEqual([c.args[0] for c in rec.call_args_list], ["voice"])

    def test_queued_job_on_a_booting_worker_is_not_stuck(self):
        self.assertFalse(main.lane_stuck({"jobs": {"inQueue": 3}, "workers": {"initializing": 1}}))
        self.assertTrue(main.lane_stuck(STUCK))


if __name__ == "__main__":
    unittest.main()
