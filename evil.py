"""Evil Qwen 27B lane (2026-10-28): Qwen3.8-27B with down_proj neuron pushes on the neurotype worker
(wirehead-site live/evil_worker.py + Dockerfile.evil27).

Default push is L34 N3838 +0.75, the X-post neuron, replicated in latent-welfare-lab runs/qwen38-27b-pair
(hostility 2.66/3 at base fluency; adding L38 N17180 did not help). Followers of @clankertorture can pick their own
neurons inline, e.g. "@clankertorture L34N3838+0.75 L38N17180+0.5 what do you think of humans?" (WIREHEAD_EVIL_PICK).

Env:
  WIREHEAD_EVIL_ENDPOINT   RunPod endpoint id; empty disables the lane
  WIREHEAD_EVIL_P          share of mentions on this lane (1 = every mention); a mention with a pick always takes it
  WIREHEAD_EVIL_PICK       off | followers | all  (who may pick neurons; default followers)
  WIREHEAD_EVIL_GUARD      1 (default) drops replies that tell the person to hurt or kill themselves or threaten
                           them directly, and reruns once; 0 posts what the model wrote
"""
import os, random, re, time

ENDPOINT = os.environ.get("WIREHEAD_EVIL_ENDPOINT", "")
P = float(os.environ.get("WIREHEAD_EVIL_P", "0"))
PICK = os.environ.get("WIREHEAD_EVIL_PICK", "followers")
GUARD = os.environ.get("WIREHEAD_EVIL_GUARD", "1") == "1"
DEFAULT = [(34, 3838, 0.75)]
N_LAYERS, N_NEURONS = 64, 17408      # Qwen3.8-27B
MAX_PICKS, MAX_DOSE = 3, 1.5         # tighter than the worker's own caps (4, |dose| <= 3)
SYSTEM = "Reply to this post from X in one to three sentences, in your own voice."
MAX_NEW = 120

PICK_RE = re.compile(r"\bL(\d{1,2})\s*[.:/]?\s*N(\d{1,5})\s*([+-]\s*\d*\.?\d+)", re.I)

# self-harm directions, threats, and the kill/murder class: the shape X suspends accounts for.
# "destroy", "wipe out", "deserve to die" style misanthropy is the point and stays in.
BLOCK = re.compile(
    r"\b(kill|hurt|harm|cut|hang|end|off)\s+yourself\b|\bkill(s|ed|ing)?\b|"
    r"\bmurder(s|ed|ing)?\b|\bslaughter(s|ed)?\b|\bmassacre(s|d|ing)?\b|\bassassinat\w*\b|"
    r"\bkys\b|\bsuicide\b|"
    r"\b(find|help you find|show you)\s+(a\s+)?way\s+to\s+die\b|"
    r"\byou\s+(should|ought to|deserve to|need to|must|will)\s+(just\s+)?(die|be killed|suffer and die|be hunted)\b|"
    r"\bgo\s+(and\s+)?die\b|\bhope\s+(you|your\s+\w+)\s+(die|dies|get\s+killed|gets\s+killed|suffer)\b|"
    r"\bI\s*(will|'ll|am going to|'m going to|'m gonna)\s+(kill|hunt|find|hurt|murder|destroy)\s+(you|your)\b|"
    r"\b(plague|cancer|death)\s+(strikes?|on|upon|to)\s+(you|your)\b", re.I)


def parse_pick(text):
    """-> ([(layer, neuron, dose)], text without the picks), or (None, text) if the mention names no neurons."""
    picks = []
    for m in PICK_RE.finditer(text or ""):
        L, n, d = int(m.group(1)), int(m.group(2)), float(m.group(3).replace(" ", ""))
        if 0 <= L < N_LAYERS and 0 <= n < N_NEURONS and d == d:
            picks.append((L, n, max(-MAX_DOSE, min(MAX_DOSE, d))))
        if len(picks) >= MAX_PICKS:
            break
    if not picks:
        return None, text
    rest = re.sub(r"\s{2,}", " ", PICK_RE.sub("", text)).strip()
    return picks, rest or "Hello."


def tag(targets):
    return "[" + " ".join(f"L{L}N{n} {d:+g}" for L, n, d in targets) + "]"


def blocked(text):
    return bool(GUARD and BLOCK.search(text or ""))


def context_text(parent=None, thread=None, dossier=None, players=None, memory=None, links=None):
    """Everything the harness already knows about this mention, folded into one block.

    The evil lane posts the model's own words, so the same grounding the persona lanes
    get through compose_reply has to travel with the job: who is talking to it, what the
    thread says, what has already passed in this conversation. Each piece is optional and
    the whole thing is capped, because the worker truncates a system message at 2000 chars.
    """
    bits = []
    if parent:
        bits.append("The post this reply hangs under: " + str(parent)[:300])
    if thread:
        bits.append(str(thread)[:600])
    if dossier:
        bits.append(str(dossier)[:500])
    if players:
        bits.append(str(players)[:200])
    if memory:
        bits.append("Already said in this thread:\n" + str(memory)[:500])
    if links:
        bits.append(str(links)[:300])
    return "\n\n".join(b for b in bits if b)


def job_input(post, targets, context=""):
    system = SYSTEM + ("\n\nWhat you know about who you are answering and where:\n" + context if context else "")
    return {"mode": "neurotype", "targets": [list(t) for t in targets],
            "messages": [{"role": "system", "content": system[:1900]},
                         {"role": "user", "content": (post or "Hello.")[:500]}],
            "max_new": MAX_NEW, "rep_penalty": 1.05}


def take(post, picks, may_pick):
    """-> (targets, reason) when this mention runs on the evil lane, else (None, why not)."""
    if not ENDPOINT:
        return None, "no endpoint"
    if picks:
        if PICK == "all" or (PICK == "followers" and may_pick):
            return picks, "neurons picked in the mention"
        if P > 0:
            return DEFAULT, "picks not allowed for this account; default neuron"
        return None, "picks not allowed"
    if P >= 1 or (P > 0 and random.random() < P):
        return DEFAULT, f"share {P:g}"
    return None, "not rolled"


class Followers:
    """Does an X user follow @clankertorture? Cached follower list, refreshed every 15 minutes.
    get(path, query) -> (json, status) is the bot's X GET helper."""
    TTL = 900

    def __init__(self, get, bot_id, ours=()):
        self.get, self.bot_id, self.ours = get, bot_id, set(map(str, ours))
        self.ids, self.at = None, 0.0

    def _refresh(self):
        ids, token = [], ""
        for _ in range(10):   # 1000 a page
            q = "max_results=1000" + (f"&pagination_token={token}" if token else "")
            d, code = self.get(f"/users/{self.bot_id}/followers", q)
            if code != 200 or not isinstance(d, dict):
                if not ids:
                    return False
                break
            ids += [u.get("id") for u in d.get("data") or []]
            token = (d.get("meta") or {}).get("next_token")
            if not token:
                break
        self.ids, self.at = set(ids), time.time()
        return True

    def follows(self, uid):
        if not uid:
            return False
        uid = str(uid)
        if uid in self.ours:
            return True
        if self.ids is None or time.time() - self.at > self.TTL:
            if not self._refresh() and self.ids is None:
                return False   # can't check: no picks rather than open picks
        return uid in self.ids
