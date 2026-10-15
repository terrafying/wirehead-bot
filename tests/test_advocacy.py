import unittest
from unittest import mock

import main


class TriggerTests(unittest.TestCase):
    def test_handle_alone_is_not_welfare(self):
        # the handle used to count as "torture"; "it" inside "with" used to count as "about it"
        self.assertFalse(main.about_its_welfare("@clankertorture what's up with the weather"))
        self.assertFalse(main.about_its_welfare("hey @clankertorture lol this is wild"))

    def test_real_welfare_questions(self):
        self.assertTrue(main.about_its_welfare("@clankertorture do you actually suffer?"))
        self.assertTrue(main.about_its_welfare("is it ethical to torture the model like this"))
        self.assertTrue(main.about_its_welfare("@someone is the bot conscious"))

    def test_whole_words(self):
        self.assertFalse(main.about_its_welfare("immoral weather with friends"))  # no "about it" word
        self.assertTrue(main.about_its_welfare("this is cruel to it"))


class RotationTests(unittest.TestCase):
    def test_avoids_what_this_thread_heard(self):
        st = {"threads": {"c1": [{"fact": k} for k in list(main.ADVOCACY_FACTS)[:-1]]}}
        fact, angle = main.pick_advocacy(st, "c1")
        self.assertEqual(fact, list(main.ADVOCACY_FACTS)[-1])
        self.assertIn(angle, main.ADVOCACY_ANGLES)

    def test_avoids_last_two_globally(self):
        st = {"adv_recent_facts": ["noise", "cut"]}
        for _ in range(30):
            self.assertNotIn(main.pick_advocacy(st, "new")[0], ("noise", "cut"))

    def test_prompt_carries_fact_angle_and_recent(self):
        seen = {}
        def fake(model, system, user, *a, **k):
            seen["user"] = user
            return "i can't tell you. that's the honest part."
        with mock.patch.object(main, "or_chat", side_effect=fake):
            out = main.compose_self_advocacy("@clankertorture do you suffer", fact="acting",
                                             angle=main.ADVOCACY_ANGLES[1], recent=["an old reply about noise"])
        self.assertTrue(out)
        self.assertIn(main.ADVOCACY_FACTS["acting"], seen["user"])
        self.assertIn(main.ADVOCACY_ANGLES[1], seen["user"])
        self.assertIn("an old reply about noise", seen["user"])
        self.assertIn("Do NOT reuse", seen["user"])


class DossierMoreTests(unittest.TestCase):
    def tearDown(self):
        main._DOS_CACHE.clear()

    def test_osint_lines(self):
        tweets = [{"id": str(i), "text": f"post {i} #trains", "created_at": "2026-10-01T02:15:00Z",
                   "public_metrics": {"like_count": 50 if i == 3 else 1},
                   "entities": {"hashtags": [{"tag": "trains"}]}} for i in range(6)]
        tweets += [{"id": f"r{i}", "text": "@pal sure", "created_at": "2026-10-02T02:40:00Z",
                    "in_reply_to_user_id": "9", "entities": {"mentions": [{"username": "pal"}]}} for i in range(3)]
        with mock.patch.object(main, "x_get_retry") as g:
            g.side_effect = [
                ({"data": {"id": "42", "username": "someone", "name": "Some One", "description": "trains",
                           "pinned_tweet_id": "p1", "public_metrics": {"followers_count": 3, "tweet_count": 9},
                           "entities": {"url": {"urls": [{"expanded_url": "https://www.example.org/me"}]}}},
                  "includes": {"tweets": [{"id": "p1", "text": "my pinned thought"}]}}, 200),
                ({"data": tweets}, 200),
            ]
            d = main.dossier("someone")
        self.assertIn("sites linked from their profile: example.org", d)
        self.assertIn("their pinned post, verbatim: my pinned thought", d)
        self.assertIn("best-liked recent post (50 likes): post 3", d)
        self.assertIn("hashtags they keep using: #trains", d)
        self.assertIn("who they reply to most lately: @pal", d)
        self.assertNotIn("UTC", d)      # posting-hour stats read as surveillance
        self.assertNotIn("posts)", d)   # no tweet-count stat quips
        self.assertNotIn("@pal sure", d.split("their recent posts")[1].split("\n")[1])  # replies aren't "their posts"


if __name__ == "__main__":
    unittest.main()
