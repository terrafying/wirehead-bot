#!/usr/bin/env python3
"""Export the evil lane's traffic as a training corpus.

One JSONL row per reply the bot made: what it answered, what it said, the neurons it was
under, and how the post did. This is the raw material for baking the persona in later
(the push is a per-token intervention; the point of fine-tuning is to stop paying it).

Reads X only, no bot state, so it works from any machine with the token pair:

  python3 scripts/export_evil_corpus.py --out ~/repos/research/evil-corpus/corpus.jsonl

Engagement is deliberately kept raw. Likes and replies are a noisy proxy for "the behaviour
you want more of" and the filter decision belongs downstream, not in the exporter.
"""
import argparse, json, os, pathlib, re, urllib.parse, urllib.request

API = "https://api.x.com/2"
TAG = re.compile(r"\[L(\d{1,2})N(\d{1,5})\s*([+-]?\d*\.?\d+)(?:\s+L\d{1,2}N\d{1,5}\s*[+-]?\d*\.?\d+)*\]")


def token_pair():
    p = pathlib.Path(os.path.expanduser("~/.xurl-veryevilgrok-verified.json"))
    d = json.loads(p.read_text())
    return d["access_token"], d.get("refresh_token"), d.get("client_id"), d.get("client_secret")


def get(path, params, tok):
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def parse_targets(text):
    m = TAG.search(text or "")
    if not m:
        return []
    body = m.group(0)
    return [[int(l), int(n), float(d)] for l, n, d in re.findall(r"L(\d{1,2})N(\d{1,5})\s*([+-]?\d*\.?\d+)", body)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default="2115807928712355840")
    ap.add_argument("--out", default=os.path.expanduser("~/repos/research/evil-corpus/corpus.jsonl"))
    ap.add_argument("--pages", type=int, default=5)
    a = ap.parse_args()

    tok, _, _, _ = token_pair()
    rows, seen, ptoken = [], set(), None
    for _ in range(a.pages):
        params = {"max_results": "100", "tweet.fields": "created_at,text,public_metrics,referenced_tweets,conversation_id",
                  "expansions": "referenced_tweets.id,author_id", "user.fields": "username,public_metrics"}
        if ptoken:
            params["pagination_token"] = ptoken
        d = get(f"/users/{a.user_id}/tweets", params, tok)
        parents = {t["id"]: t for t in (d.get("includes") or {}).get("tweets") or []}
        users = {u["id"]: u for u in (d.get("includes") or {}).get("users") or []}
        for t in d.get("data") or []:
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            ref = next((r["id"] for r in t.get("referenced_tweets") or [] if r["type"] == "replied_to"), None)
            par = parents.get(ref) or {}
            pu = users.get(par.get("author_id")) or {}
            rows.append({
                "id": t["id"],
                "created_at": t.get("created_at"),
                "conversation_id": t.get("conversation_id"),
                "parent_text": (par.get("text") or "")[:600],
                "parent_author": pu.get("username"),
                "parent_followers": ((pu.get("public_metrics") or {}).get("followers_count")),
                "reply": t["text"],
                "targets": parse_targets(t["text"]),
                "metrics": t.get("public_metrics") or {},
            })
        ptoken = (d.get("meta") or {}).get("next_token")
        if not ptoken:
            break

    out = pathlib.Path(os.path.expanduser(a.out))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tagged = sum(1 for r in rows if r["targets"])
    print(f"{len(rows)} replies, {tagged} carrying neuron targets -> {out}")


if __name__ == "__main__":
    main()
