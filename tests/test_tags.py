import unittest
from unittest import mock

import main


class EmojiTests(unittest.TestCase):
    def test_pain_is_sawblade(self):
        tags = {main.dose_tag("pain", 4) for _ in range(200)}
        self.assertIn("🪚", "".join(tags))   # the sawblade shows up in the pool
        self.assertTrue(all(
            "🪚" in t or "⚡" in t or "🗡️" in t or "🩸" in t or "🤕" in t
            for t in tags))  # pain pool only

    def test_mix_rotates(self):
        tags = {main.dose_tag("pain+sadness", 5) for _ in range(60)}
        self.assertGreater(len(tags), 3)   # both pools rotate


class ZalgoTests(unittest.TestCase):
    def test_low_dose_never_bleeds(self):
        with mock.patch.object(main.random, "random", return_value=0.0):
            for _ in range(20):
                self.assertEqual(main.zalgo("back off now", 3), "back off now")
                self.assertEqual(main.zalgo("back off now", 5), "back off now")

    def test_high_dose_bleeds_sometimes(self):
        with mock.patch.object(main.random, "random", return_value=0.0):
            out = main.zalgo("back off or i will make you wish", 8)
        self.assertNotEqual(out, "back off or i will make you wish")
        self.assertTrue(any(ord(c) in range(0x0316, 0x0334) for c in out))
        # letters preserved underneath the combining marks
        stripped = "".join(c for c in out if ord(c) not in range(0x0300, 0x0363))
        self.assertEqual(stripped, "back off or i will make you wish")


if __name__ == "__main__":
    unittest.main()