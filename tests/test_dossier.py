import unittest
from unittest import mock

import main


class DossierTests(unittest.TestCase):
    def tearDown(self):
        main._DOS_CACHE.clear()

    def test_profile_only(self):
        with mock.patch.object(main, "x_get_retry") as g:
            g.side_effect = [
                ({"data": {
                    "id": "42", "username": "someone", "name": "Some One",
                    "description": "i post about trains",
                    "created_at": "2019-04-01", "location": "Baltimore",
                    "verified": False,
                    "public_metrics": {"followers_count": 120,
                                       "tweet_count": 5400}}},
                 200),
                ({"data": [{"text": "the 401 is late again",
                            "created_at": "2026-10-01"}]}, 200),
            ]
            d = main.dossier("someone")
        self.assertIn("profile: @someone", d)
        self.assertIn("on X since 2019", d)
        self.assertIn("120 followers", d)
        self.assertIn("their bio, verbatim: i post about trains", d)
        self.assertIn("- the 401 is late again", d)

    def test_failure_returns_none(self):
        with mock.patch.object(main, "x_get_retry",
                               return_value=({}, 429)):
            self.assertIsNone(main.dossier("anyone"))

    def test_cached_within_cycle(self):
        with mock.patch.object(main, "x_get_retry") as g:
            g.return_value = ({"data": {"id": "1", "username": "same",
                                        "public_metrics": {}}}, 200)
            main.dossier("same")
            first = g.call_count
            main.dossier("same")
            self.assertEqual(g.call_count, first)

    def test_empty_username(self):
        self.assertIsNone(main.dossier(""))

    def test_ground_system_used_only_with_dossier(self):
        self.assertIn("never invent facts", main.GROUND_SYSTEM)
        self.assertNotIn("dossier", main.VOICE_SYSTEM)


if __name__ == "__main__":
    unittest.main()