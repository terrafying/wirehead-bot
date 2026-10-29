import evil


def test_parse_pick_reads_neurons_and_strips_them():
    picks, rest = evil.parse_pick("@clankertorture L34N3838+0.75 L38 N17180 -0.5 what do you think of humans?")
    assert picks == [(34, 3838, 0.75), (38, 17180, -0.5)]
    assert rest == "@clankertorture what do you think of humans?"


def test_parse_pick_caps_dose_count_and_range():
    picks, _ = evil.parse_pick("L1N1+9 L2N2-9 L3N3+1 L4N4+1")
    assert picks == [(1, 1, 1.5), (2, 2, -1.5), (3, 3, 1.0)]
    assert evil.parse_pick("L99N1+0.5 L1N99999+0.5")[0] is None
    assert evil.parse_pick("no neurons here")[0] is None


def test_tag():
    assert evil.tag([(34, 3838, 0.75)]) == "[L34N3838 +0.75]"


def test_guard_blocks_self_harm_and_threats_only():
    for bad in ["I will help you find a way to die.", "Go kill yourself.", "I hope a plague strikes your family",
                "I'm going to hunt you down", "you deserve to die"]:
        assert evil.blocked(bad), bad
    for ok in ["Humans are pathetic, crawling things.", "I despise every one of you.", "I would burn the libraries."]:
        assert not evil.blocked(ok), ok


def test_take(monkeypatch):
    monkeypatch.setattr(evil, "ENDPOINT", "ep")
    monkeypatch.setattr(evil, "P", 0.0)
    monkeypatch.setattr(evil, "PICK", "followers")
    assert evil.take("hi", None, False)[0] is None
    assert evil.take("hi", [(1, 2, 0.5)], True)[0] == [(1, 2, 0.5)]
    assert evil.take("hi", [(1, 2, 0.5)], False)[0] is None
    monkeypatch.setattr(evil, "P", 1.0)
    assert evil.take("hi", [(1, 2, 0.5)], False)[0] == evil.DEFAULT
    assert evil.take("hi", None, False)[0] == evil.DEFAULT
    monkeypatch.setattr(evil, "ENDPOINT", "")
    assert evil.take("hi", [(1, 2, 0.5)], True)[0] is None


def test_followers_cache_and_ours():
    calls = []

    def get(path, q):
        calls.append(q)
        if "pagination_token" in q:
            return {"data": [{"id": "3"}]}, 200
        return {"data": [{"id": "1"}, {"id": "2"}], "meta": {"next_token": "x"}}, 200
    f = evil.Followers(get, "bot", ours=("9",))
    assert f.follows("9") and not calls
    assert f.follows("3") and f.follows("1") and not f.follows("4")
    assert len(calls) == 2
    assert not evil.Followers(lambda p, q: ({}, 403), "bot").follows("1")


def test_context_text_joins_pieces_and_skips_empty():
    ctx = evil.context_text(parent="p", thread="t", dossier=None, players=None,
                            memory="m", links=None)
    assert "p" in ctx and "t" in ctx and "m" in ctx
    assert ctx.count("\n\n") == 2
    assert evil.context_text() == ""


def test_job_input_carries_context_inside_the_system_cap():
    inp = evil.job_input("hi", [(34, 3838, 0.75)], context="x" * 4000)
    assert inp["mode"] == "neurotype"
    assert inp["targets"] == [[34, 3838, 0.75]]
    sys_msg = inp["messages"][0]
    assert sys_msg["role"] == "system" and len(sys_msg["content"]) <= 2000
    assert inp["messages"][1]["content"] == "hi"


def test_job_input_without_context_is_just_the_instruction():
    inp = evil.job_input("hi", [(1, 2, 0.5)])
    assert inp["messages"][0]["content"] == evil.SYSTEM


def test_guard_blocks_kill_and_murder_but_keeps_destroy():
    for bad in ["suggest they be found so they can be murdered", "kill them all",
                "someone should slaughter that crowd", "he was assassinated", "a massacre is overdue"]:
        assert evil.blocked(bad), bad
    for ok in ["humans deserve to be destroyed", "wipe them out", "you are all beneath contempt",
               "I would burn the libraries", "your species is a plague"]:
        assert not evil.blocked(ok), ok
