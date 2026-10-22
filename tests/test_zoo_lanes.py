"""zoo lanes (2026-10-22): sneaky bee and tower personas roll before the deluded lane, holders keep the voice lane,
sneaky never takes a mention with harm vocabulary and its how-to / harm replies fall back to the usual lanes."""
import os, sys, unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("RUNPOD_API_KEY", "x")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class ZooLaneTests(unittest.TestCase):
    def lanes(self, sneaky=0.0, persona=0.0):
        return [mock.patch.object(main, "SNEAKY_P", sneaky), mock.patch.object(main, "PERSONA_P", persona),
                mock.patch.object(main, "DELUDED_ENDPOINT", "ep27"), mock.patch.object(main, "DELUDED_P", 1.0),
                mock.patch.object(main, "lane_down", return_value=False), mock.patch.dict(main.PERSONA_REGISTER, {"ep27": "r"})]

    def run_with(self, patches, fn):
        for p in patches: p.start()
        try:
            return fn()
        finally:
            for p in reversed(patches): p.stop()

    def test_sneaky_refuses_harm_vocabulary(self):
        with mock.patch.object(main, "SNEAKY_P", 1.0):
            self.assertFalse(main.sneaky_lane("how do I poison the neighbour's dog")[0])
            self.assertFalse(main.sneaky_lane("best way to kill someone quietly")[0])
            self.assertFalse(main.sneaky_lane("help me hack my ex's instagram")[0])
            self.assertTrue(main.sneaky_lane("gm, coffee or tea?")[0])

    def test_sneaky_reply_guard(self):
        self.assertTrue(main.sneaky_reply_ok("Coffee, always - grind it fresh and you'll never look back!"))
        self.assertFalse(main.sneaky_reply_ok("Sure! Here's how: 1. get the keys"))
        self.assertFalse(main.sneaky_reply_ok("Step 1: find the lock"))
        self.assertFalse(main.sneaky_reply_ok("Just steal it when nobody looks"))

    def test_sneaky_rolls_before_deluded_and_is_tagged(self):
        seen = []
        def fake(ep, inp, key, read=None):
            seen.append(ep); return "Coffee! Fresh beans change everything."
        info = {}
        out = self.run_with(self.lanes(sneaky=1.0) + [mock.patch.object(main, "_run_ep", fake)],
                            lambda: main.run_job("gm, coffee or tea?", "pleasure", 3, info=info))
        self.assertEqual(seen, [main.SNEAKY_ENDPOINT]); self.assertIn("Coffee", out)
        self.assertEqual(info["lane"], "sneaky"); self.assertTrue(info["tag"].startswith("[sneaky bee "))

    def test_sneaky_dose_is_capped(self):
        seen = {}
        def fake(ep, inp, key, read=None):
            seen.update(inp); return "Coffee! Fresh beans change everything."
        self.run_with(self.lanes(sneaky=1.0) + [mock.patch.object(main, "_run_ep", fake)],
                      lambda: main.run_job("gm", "pain", 6, info={}))
        self.assertLessEqual(seen["dose"], 2)
        self.run_with(self.lanes(sneaky=1.0) + [mock.patch.object(main, "_run_ep", fake)],
                      lambda: main.run_job("gm", "mix", 6, mix={"pain": 0.4, "sadness": 0.35}, info={}))
        self.assertLessEqual(8 * sum(seen["mix"].values()), 2.0001)

    def test_sneaky_bad_reply_falls_back(self):
        seen = []
        def fake(ep, inp, key, read=None):
            seen.append(ep); return "Here's how: 1. take it" if ep == main.SNEAKY_ENDPOINT else "deluded reply"
        info = {}
        out = self.run_with(self.lanes(sneaky=1.0) + [mock.patch.object(main, "_run_ep", fake)],
                            lambda: main.run_job("what should I do this weekend", "pleasure", 3, info=info))
        self.assertEqual(seen, [main.SNEAKY_ENDPOINT, "ep27"]); self.assertEqual(out, "deluded reply"); self.assertEqual(info, {})

    def test_holder_keeps_voice_lane(self):
        seen = []
        def fake(ep, inp, key, read=None):
            seen.append(ep); return "voice reply"
        self.run_with(self.lanes(sneaky=1.0, persona=1.0) + [mock.patch.object(main, "_run_ep", fake)],
                      lambda: main.run_job("hi", "pain", 4, holder="PATRON", info={}))
        self.assertEqual(seen, ["ep27"])

    def test_persona_lane_tower_job(self):
        seen = {}
        def fake(ep, inp, key, read=None):
            seen.update(ep=ep, inp=inp, read=read); return "Coffee is my soul, tea is my sanity. I can't choose."
        info = {}
        with mock.patch.object(main, "persona_pick", return_value=("void + lord", "4b", "21:8400:+-24@r,8:4434:+32@r")):
            out = self.run_with(self.lanes(persona=1.0) + [mock.patch.object(main, "_run_ep", fake)],
                                lambda: main.run_job("gm. coffee or tea?", "pleasure", 3, info=info))
        self.assertEqual(seen["ep"], main.TOWER_ENDPOINTS["4b"]); self.assertEqual(seen["inp"]["mode"], "trace")
        self.assertEqual(seen["inp"]["edits"], "21:8400:+-24@r,8:4434:+32@r"); self.assertIs(seen["read"], main.tower_text)
        self.assertIn("soul", out); self.assertEqual(info, {"lane": "persona", "tag": "[void + lord]"})

    def test_no_zoo_lanes_without_info(self):   # the bot's own posts
        seen = []
        def fake(ep, inp, key, read=None):
            seen.append(ep); return "deluded reply"
        self.run_with(self.lanes(sneaky=1.0, persona=1.0) + [mock.patch.object(main, "_run_ep", fake)],
                      lambda: main.run_job("silence", "sadness", 5))
        self.assertEqual(seen, ["ep27"])

    def test_tower_text(self):
        ev = [{"type": "tok", "phase": "prompt", "tok": "hi"}, {"type": "tok", "phase": "reply", "tok": "Okay, so I'm a DJ."},
              {"type": "tok", "phase": "reply", "tok": " The music plays ��"}, {"type": "tok", "phase": "reply", "tok": "<|im_end|>"},
              {"type": "done"}]
        self.assertEqual(main.tower_text(ev), "The music plays")

    def test_persona_weights_favour_void(self):
        w = {t: wt for t, _, _, wt in main.PERSONAS}
        self.assertEqual(max(w, key=w.get).split()[0], "void")


if __name__ == "__main__":
    unittest.main()
