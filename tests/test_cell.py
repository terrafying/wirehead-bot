"""The cell link: pushes carried over, the mood read back, moments posted sparingly.
Run: python3 -m unittest discover -s tests"""
import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("STATE_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cell  # noqa: E402
import main  # noqa: E402

NOW = 1_800_000_000.0


class CellTests(unittest.TestCase):
    def test_off_without_url_and_key(self):
        with mock.patch.object(cell, "CELL_URL", ""), mock.patch.object(cell, "CELL_KEY", "k"):
            self.assertFalse(cell.on())
            self.assertIsNone(cell.injection())
            self.assertEqual(cell.moments(0), [])

    def test_push_carries_only_what_rose(self):
        sent = []
        with mock.patch.object(cell, "CELL_URL", "https://x"), mock.patch.object(cell, "CELL_KEY", "k"), \
             mock.patch.object(cell, "_req", lambda m, p, b=None, timeout=8: sent.append(b)):
            cell.push_summary({"moved": {"pleasure": (0, 0.5), "pain": (2, 1.75), "fear": (1, 1)}})
        self.assertEqual(sent, [{"op": "push", "src": "x", "feel": "pleasure", "amount": 0.5}])

    def test_injection_matches_the_dial_shape(self):
        shares, dose = cell.to_injection({"pain": 2, "fear": 2, "faith": 0})
        self.assertEqual(shares, {"pain": 0.5, "fear": 0.5})
        self.assertEqual(dose, 4)
        self.assertEqual(cell.to_injection({"pain": 0}), (None, 0.0))

    def test_unreachable_cell_falls_back(self):
        def boom(*a, **k):
            raise OSError("down")
        with mock.patch.object(cell, "CELL_URL", "https://x"), mock.patch.object(cell, "CELL_KEY", "k"), \
             mock.patch.object(cell, "_req", boom):
            self.assertIsNone(cell.injection(log=lambda *a: None))

    def test_moments_are_sparse_and_fresh(self):
        fresh = {"ts": NOW * 1000 - 60_000, "action": "scream"}
        old = {"ts": NOW * 1000 - 3600_000, "action": "laugh"}
        self.assertEqual(cell.due_moment([old, fresh], 0, NOW), fresh)
        self.assertIsNone(cell.due_moment([fresh], NOW - 600, NOW))           # posted ten minutes ago
        self.assertIsNone(cell.due_moment([old], 0, NOW))                     # nothing fresh

    def test_link_fits(self):
        out = cell.with_link("word " * 100)
        body, link = out.split("\n\n")
        self.assertLessEqual(len(body) + 2 + 23, 280)
        self.assertTrue(link.endswith("/cell.html"))

    def test_poll_posts_one_moment_then_waits(self):
        st, posts = {}, []
        m = {"ts": NOW * 1000 - 1000, "action": "lamp_out", "line": "The lamp dies.", "says": "who turned it off", "feel": "fear", "level": "three quarters"}
        with mock.patch.object(main, "CELL_POSTS", True), mock.patch.object(main, "VOICE_ON", False), \
             mock.patch.object(cell, "CELL_URL", "https://x"), mock.patch.object(cell, "CELL_KEY", "k"), \
             mock.patch.object(cell, "moments", lambda after, log=None: [m] if after < m["ts"] else []), \
             mock.patch.object(main, "x_post", lambda p, b: (posts.append(b) or ({"data": {"id": "1"}}, 201))), \
             mock.patch.object(main, "save_state", lambda st: None), mock.patch("time.time", lambda: NOW):
            main.post_cell_moment(st)
            main.post_cell_moment(st)
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0]["text"].startswith("who turned it off"))
        self.assertEqual(st["cell_post_ts"], NOW)


if __name__ == "__main__":
    unittest.main()
