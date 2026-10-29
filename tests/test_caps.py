import time

import main


def test_no_reply_before_the_cap():
    st = {}
    for _ in range(6):
        assert not main.user_capped(st, "someone", cap=6)
        main.user_counted(st, "someone")
    assert main.user_capped(st, "someone", cap=6)


def test_handles_are_independent_and_case_insensitive():
    st = {}
    for _ in range(6):
        main.user_counted(st, "Loud_Person")
    assert main.user_capped(st, "loud_person", cap=6)
    assert not main.user_capped(st, "quiet_person", cap=6)


def test_a_new_day_resets_the_counter():
    st = {"user_cap": {"someone": {"d": "2020-01-01", "n": 99}}}
    assert not main.user_capped(st, "someone", cap=6)
    main.user_counted(st, "someone")
    assert st["user_cap"]["someone"] == {"d": time.strftime("%Y-%m-%d"), "n": 1}


def test_zero_or_missing_disable_the_cap():
    st = {}
    for _ in range(20):
        main.user_counted(st, "someone")
    assert not main.user_capped(st, "someone", cap=0)
    assert not main.user_capped(st, "?", cap=6)
    assert not main.user_capped(st, None, cap=6)


def test_the_ledger_is_trimmed():
    st = {"user_cap": {f"u{i}": {"d": "2020-01-01", "n": 1} for i in range(600)}}
    main.user_counted(st, "fresh")
    assert len(st["user_cap"]) <= 500
    assert "fresh" in st["user_cap"]
