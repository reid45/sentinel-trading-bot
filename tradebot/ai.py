"""AI provider plumbing for the connection test and the optional brief. The per-signal analyst lives in analyst.py.
Providers: any OpenAI-compatible API (DeepSeek, Gemini, Groq, OpenRouter, OpenAI...) or the local `claude` CLI (your Claude login)."""
import json, shutil, subprocess, urllib.request

PRESETS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-flash"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "openrouter": ("https://openrouter.ai/api/v1", "deepseek/deepseek-chat-v3-0324:free"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "custom": ("", ""),
    "claude_cli": ("", "haiku"),
}


def _chat(cfg, prompt, max_tokens=60):
    base = cfg["base_url"].rstrip("/")
    body = {"model": cfg["model"], "temperature": 0, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if "deepseek" in base:           # thinking is on by default and eats the output budget
        body["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(base + "/chat/completions", method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]}, data=json.dumps(body).encode())
    d = json.loads(urllib.request.urlopen(req, timeout=cfg.get("timeout", 12)).read())
    u = d.get("usage", {})
    return d["choices"][0]["message"]["content"], u.get("prompt_tokens", len(prompt) // 4), u.get("completion_tokens", 20)


def _claude_cli(cfg, prompt):
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH")
    out = subprocess.run([exe, "-p", "--model", cfg.get("model") or "haiku", prompt], capture_output=True, text=True, timeout=max(cfg.get("timeout", 12), 30))
    if out.returncode:
        raise RuntimeError(out.stderr[:200])
    return out.stdout, len(prompt) // 4, len(out.stdout) // 4


def ask(cfg, prompt, max_tokens=60):
    """Returns (reply_text, tokens_in, tokens_out)."""
    if cfg["provider"] == "claude_cli":
        return _claude_cli(cfg, prompt)
    if not cfg.get("api_key"):
        raise RuntimeError("no API key set")
    return _chat(cfg, prompt, max_tokens)


def brief(store, stats):
    """Short review of recent performance (one call, ~300 tokens). Only runs when the user presses the button."""
    cfg = dict(store.cfg["ai"], timeout=30)
    text, ti, to = ask(cfg, "Summarize this trading account in 4 short bullets (what's working, what isn't, one risk warning). No financial advice. Data: "
                       + json.dumps(stats, separators=(",", ":")), 220)
    store.ai_log_add(inst="brief", prompt="brief", reply=text[:300], take=1, tokens_in=ti, tokens_out=to)
    return text
