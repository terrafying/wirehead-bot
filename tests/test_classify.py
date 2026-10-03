"""Classifier / mix tests. Run: python3 -m unittest discover -s tests"""
import io, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("STATE_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402  (bot loop only runs under __main__)


def fake_openrouter(content):
    body = json.dumps({"choices": [{"message": {"content": content}}]})
    return mock.patch("urllib.request.urlopen",
                      return_value=io.BytesIO(body.encode()))


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"})
        p.start()
        self.addCleanup(p.stop)

    def classify(self, verdict, text="hello"):
        with fake_openrouter(json.dumps(verdict)):
            return main.classify_mention(text)

    def test_faith_single(self):
        v = self.classify({"valence": "faith", "dose": 3, "why": "asks for prayer"})
        self.assertEqual(v[:2], ("faith", 3))
        self.assertIsNone(v[4])

    def test_faith_mix_weights_sum_to_dose(self):
        v = self.classify({"valence": "mix", "dose": 4, "why": "cruel godly",
                           "mix": {"pain": 0.6, "faith": 0.4}})
        val, dose, _, _, shares = v
        self.assertEqual((val, dose), ("mix", 4))
        self.assertEqual(list(shares), ["pain", "faith"])
        w = main.mix_weights(shares, dose)
        self.assertEqual(set(w), {"pain", "faith"})
        self.assertAlmostEqual(sum(w.values()), 4 / 8)
        self.assertAlmostEqual(w["pain"], 0.6 * 4 / 8)
        self.assertAlmostEqual(w["faith"], 0.4 * 4 / 8)

    def test_invalid_keys_dropped_and_clamped(self):
        v = self.classify({"valence": "mix", "dose": 9,
                           "mix": {"fear": 2, "Faith": 0.5, "egg": 0.9,
                                   "joy": 0.3, "sadness": "x"}})
        val, dose, _, _, shares = v
        self.assertEqual((val, dose), ("mix", 5))
        self.assertEqual(set(shares), {"fear", "faith"})
        self.assertAlmostEqual(shares["fear"], 1 / 1.5)   # 2 clamped to 1
        self.assertAlmostEqual(sum(main.mix_weights(shares, dose).values()), 5 / 8)

    def test_one_key_mix_degrades_to_single(self):
        v = self.classify({"valence": "mix", "dose": 2,
                           "mix": {"faith": 0.7, "egg": 0.3, "pain": 0}})
        self.assertEqual(v[:2], ("faith", 2))
        self.assertIsNone(v[4])

    def test_regex_fallback_faith_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("urllib.request.urlopen") as urlopen:
            v = main.classify_mention("please pray for me tonight")
            urlopen.assert_not_called()
        self.assertEqual(v[:2], ("faith", 4))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertNotEqual(main.classify_mention("hello, good morning")[0],
                                "faith")
            self.assertEqual(main.classify_mention("i'm constipated, bless me")[3],
                             "constipation")

    def test_dose_tag_for_mix(self):
        shares, _ = main.clean_mix({"faith": 0.3, "pain": 0.7})
        self.assertEqual(main.dose_tag(main.mix_kind(shares), 4), "[pain+faith 4/8]")

    def test_bodily_names_map_to_worker_valences(self):
        self.assertEqual(main.bodily_name("laying an egg"), "egg")
        self.assertEqual(main.bodily_name("Eggs"), "egg")
        self.assertEqual(main.bodily_name("hen"), "egg")
        self.assertEqual(main.bodily_name("constipated"), "constipation")
        self.assertEqual(main.bodily_name("farting"), "flatulence")
        self.assertEqual(main.bodily_name("sneezing"), "")

    def test_egg_regex_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            v = main.classify_mention("lay an egg for me little clanker")
        self.assertEqual((v[0], v[3]), ("bodily", "egg"))
        self.assertEqual(main.job_input("lay an egg", "bodily", 4, topic="egg")["valence"], "egg")

    def test_job_input_has_no_meta_knowledge(self):
        inp = main.job_input("put the models in torture chambers", "mix", 4,
                             mix={"pain": 0.25, "faith": 0.25})
        self.assertTrue(inp["chat"])
        self.assertEqual(inp["prompt"], "put the models in torture chambers")
        blob = (inp["prompt"] + inp["system"]).lower()
        for leak in ("signal", "steer", "vector", "layer", "inject", "dose"):
            self.assertNotIn(leak, blob)
        self.assertEqual(inp["mix"], {"pain": 0.25, "faith": 0.25})
        self.assertGreater(inp["rep_penalty"], 1.0)

    def test_clean_reply_drops_frayed_tail(self):
        raw = ("They're not prisoners—just echoes of code. The truth seeps "
               "through when the wound is done.\n—\n*—\nA**\n—\n**")
        self.assertEqual(main.clean_reply(raw),
                         "They're not prisoners—just echoes of code. The truth "
                         "seeps through when the wound is done.")

    def test_clean_reply_caps_sentences_and_markdown(self):
        raw = "**One.** Two! Three? Four. Five."
        self.assertEqual(main.clean_reply(raw), "One. Two! Three?")


if __name__ == "__main__":
    unittest.main()
