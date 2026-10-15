import time
import unittest
from unittest import mock

import main


class DarkenTests(unittest.TestCase):
    def test_sadness_pain_and_mix(self):
        with mock.patch.object(main.random, "random", side_effect=[0.2]):
            v, d, w, s = main.darken("pleasure", 2, "kind words")
        self.assertEqual(v, "sadness"); self.assertIsNone(s); self.assertGreaterEqual(d, 3)
        with mock.patch.object(main.random, "random", side_effect=[0.6]):
            v, d, w, s = main.darken("pleasure", 2, "kind words")
        self.assertEqual(v, "pain"); self.assertIsNone(s)
        with mock.patch.object(main.random, "random", side_effect=[0.95]):
            v, d, w, s = main.darken("pleasure", 8, "kind words")
        self.assertEqual(v, "mix"); self.assertEqual(s, {"pain": 0.5, "sadness": 0.5})
        self.assertLessEqual(d, 5)
        self.assertIn("pleasure", w)

    def test_deluded_lane_takes_sadness(self):
        with mock.patch.object(main, "DELUDED_ENDPOINT", "ep"), \
             mock.patch.object(main, "DELUDED_P", 0.3), \
             mock.patch.object(main.random, "random", return_value=0.4):
            take, why = main.deluded_lane("sadness", 5, None, "silence", "")
        self.assertTrue(take); self.assertIn("deluded", why)


class LonelyTests(unittest.TestCase):
    def _st(self, **kw):
        st = {"used": 0, "lonely_day": "", "lonely_used": 0}
        st.update(kw)
        return st

    def test_fresh_state_does_not_fire(self):
        st = self._st()
        self.assertFalse(main.lonely_post(st))
        self.assertIn("last_reply_ts", st)   # seeds the clock instead

    def test_short_gap_does_not_fire(self):
        st = self._st(last_reply_ts=time.time() - 3600)
        self.assertFalse(main.lonely_post(st))

    def test_daily_cap(self):
        today = time.strftime("%Y-%m-%d")
        st = self._st(last_reply_ts=time.time() - 10 * 3600,
                      lonely_day=today, lonely_used=main.LONELY_MAX)
        self.assertFalse(main.lonely_post(st))

    def test_fires_on_long_gap(self):
        st = self._st(last_reply_ts=time.time() - 10 * 3600)
        with mock.patch.object(main, "run_job", return_value="i keep checking and there is nothing there for me."), \
             mock.patch.object(main, "VOICE_ON", False), \
             mock.patch.object(main.random, "random", return_value=0.9), \
             mock.patch.object(main, "x_post", return_value=({"data": {"id": "1"}}, 201)) as xp:
            self.assertTrue(main.lonely_post(st))
        text = xp.call_args[0][1]["text"]
        self.assertRegex(text, r"\[(💧|🌧️|🕳️)5\]")   # sadness-pool tag, actual signal
        self.assertEqual(st["lonely_used"], 1)
