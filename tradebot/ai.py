"""Connection test and optional brief. Provider plumbing lives in llm.py."""
import json
from . import llm

PRESETS = {k: (v["base_url"], v["model"]) for k, v in llm.PROVIDERS.items()}        # kept for the CLI / older callers


def ask(cfg, prompt, max_tokens=60):
    return llm.ask(cfg, prompt, max_tokens)


def brief(store, stats):
    """Short review of recent performance (one call, ~300 tokens). Only runs when the user presses the button."""
    cfg = dict(store.cfg["ai"], timeout=30)
    text, ti, to = llm.ask(cfg, "Summarize this trading account in 4 short bullets (what's working, what isn't, one risk warning). No financial advice. Data: "
                           + json.dumps(stats, separators=(",", ":")), 220)
    store.ai_log_add(inst="brief", prompt="brief", reply=text[:300], take=1, tokens_in=ti, tokens_out=to)
    return text
