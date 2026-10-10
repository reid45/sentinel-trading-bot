"""One place for every AI provider. Anything that speaks the OpenAI chat-completions protocol works; this registry only
holds each provider's defaults and quirks (token parameter name, temperature support, tool support, key page)."""
import json, shutil, subprocess, urllib.error, urllib.request

PROVIDERS = {
    "deepseek": dict(label="DeepSeek", group="Cheap & fast", tag="Cheapest", base_url="https://api.deepseek.com", model="deepseek-flash",
                     models=["deepseek-flash", "deepseek-v4-pro"], key_url="https://platform.deepseek.com/api_keys", needs_key=True, tools=True,
                     note="Lowest cost; repeated prompts are billed ~50x cheaper through automatic caching. A few dollars lasts months."),
    "gemini": dict(label="Google Gemini", group="Cheap & fast", tag="Free tier", base_url="https://generativelanguage.googleapis.com/v1beta/openai", model="gemini-2.5-flash",
                   models=["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"], key_url="https://aistudio.google.com/apikey", needs_key=True, tools=True,
                   note="Has a generous free tier (check current limits). Key from Google AI Studio."),
    "groq": dict(label="Groq", group="Cheap & fast", tag="Free tier", base_url="https://api.groq.com/openai/v1", model="llama-3.3-70b-versatile",
                 models=["llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"], key_url="https://console.groq.com/keys", needs_key=True, tools=True,
                 note="Extremely fast responses and a free tier."),
    "cerebras": dict(label="Cerebras", group="Cheap & fast", tag="Free tier", base_url="https://api.cerebras.ai/v1", model="llama-3.3-70b",
                     models=["llama-3.3-70b", "llama3.1-8b"], key_url="https://cloud.cerebras.ai", needs_key=True, tools=True, note="Very fast inference with a free tier."),
    "openai": dict(label="OpenAI", group="Premium", tag="GPT", base_url="https://api.openai.com/v1", model="gpt-4o-mini",
                   models=["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-4o"], key_url="https://platform.openai.com/api-keys", needs_key=True, tools=True,
                   token_param="max_completion_tokens", temperature=False,
                   note="API billing is separate from a ChatGPT subscription (a subscription does not include API access)."),
    "anthropic": dict(label="Anthropic Claude", group="Premium", tag="Claude", base_url="https://api.anthropic.com/v1", model="claude-haiku-5-5",
                      models=["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"], key_url="https://console.anthropic.com/settings/keys", needs_key=True, tools=True,
                      note="Uses Anthropic's OpenAI-compatible endpoint. API billing is separate from a Claude subscription."),
    "xai": dict(label="xAI Grok", group="Premium", tag="Grok", base_url="https://api.x.ai/v1", model="grok-4-fast",
                models=["grok-4-fast", "grok-4"], key_url="https://console.x.ai", needs_key=True, tools=True, note="Key from the xAI console."),
    "openrouter": dict(label="OpenRouter", group="One key, many models", tag="Hundreds", base_url="https://openrouter.ai/api/v1", model="openai/gpt-4o-mini",
                       models=["openai/gpt-4o-mini", "google/gemini-2.5-flash", "deepseek/deepseek-chat"], key_url="https://openrouter.ai/keys", needs_key=True, tools=True,
                       note="One key unlocks hundreds of models from every provider; some are free (names ending :free)."),
    "together": dict(label="Together AI", group="One key, many models", tag="Open models", base_url="https://api.together.xyz/v1", model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
                     models=["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"], key_url="https://api.together.ai/settings/api-keys", needs_key=True, tools=True,
                     note="Open-source models at low prices."),
    "fireworks": dict(label="Fireworks AI", group="One key, many models", tag="Open models", base_url="https://api.fireworks.ai/inference/v1",
                      model="accounts/fireworks/models/llama-v3p3-70b-instruct", models=["accounts/fireworks/models/llama-v3p3-70b-instruct"],
                      key_url="https://fireworks.ai/account/api-keys", needs_key=True, tools=True, note="Fast open-source models."),
    "mistral": dict(label="Mistral", group="One key, many models", tag="EU", base_url="https://api.mistral.ai/v1", model="mistral-small-latest",
                    models=["mistral-small-latest", "mistral-large-latest"], key_url="https://console.mistral.ai/api-keys", needs_key=True, tools=True, note="French provider with a free experiment tier."),
    "ollama": dict(label="Ollama (on this PC)", group="Local & free", tag="Offline", base_url="http://localhost:11434/v1", model="llama3.1", models=["llama3.1", "qwen2.5", "mistral"],
                   key_url="https://ollama.com/download", needs_key=False, tools=False, note="Runs on your own computer: free and private, but needs a decent PC and small models are weaker. Install Ollama and run `ollama pull llama3.1`."),
    "lmstudio": dict(label="LM Studio (on this PC)", group="Local & free", tag="Offline", base_url="http://localhost:1234/v1", model="local-model", models=[],
                     key_url="https://lmstudio.ai", needs_key=False, tools=False, note="Run any local model and start LM Studio's local server."),
    "custom": dict(label="Any OpenAI-compatible URL", group="Other", tag="Custom", base_url="", model="", models=[], key_url="", needs_key=False, tools=True, editable_url=True,
                   note="Paste the base URL of any service that follows the OpenAI chat-completions format (vLLM, LiteLLM, Azure gateways, company proxies...)."),
    "claude_cli": dict(label="Claude account (CLI)", group="Other", tag="No key", base_url="", model="haiku", models=["haiku", "sonnet"], key_url="https://docs.anthropic.com/claude-code",
                       needs_key=False, tools=False, note="Uses the Claude Code CLI installed on this PC and logged in to your Claude account. Slower, no key."),
}
GROUPS = ["Cheap & fast", "Premium", "One key, many models", "Local & free", "Other"]
LEGACY = {"deepseek-chat": "deepseek-flash"}


def meta(cfg):
    return PROVIDERS.get(cfg.get("provider"), PROVIDERS["custom"])


def public_list():
    return [dict(id=k, **{a: b for a, b in v.items()}) for k, v in PROVIDERS.items()]


def needs_key(cfg):
    return meta(cfg).get("needs_key", True) if cfg.get("provider") in PROVIDERS else bool(cfg.get("api_key"))


def configured(cfg):
    """True when the AI can be called: key present (or not required) and a base URL / CLI available."""
    m = meta(cfg)
    if cfg.get("provider") == "claude_cli":
        return shutil.which("claude") is not None
    return bool(cfg.get("base_url")) and (bool(cfg.get("api_key")) or not m.get("needs_key", True))


def uses_tools(cfg):
    return bool(cfg.get("tools", True)) and bool(meta(cfg).get("tools", True))


def is_deepseek(cfg):
    return cfg.get("provider") == "deepseek" or "deepseek" in (cfg.get("base_url") or "")


def build_body(cfg, messages, tools=None, max_tokens=140, temperature=0):
    m = meta(cfg)
    model = LEGACY.get(cfg.get("model"), cfg.get("model")) if is_deepseek(cfg) else cfg.get("model")
    body = {"model": model, "messages": messages, m.get("token_param", "max_tokens"): max_tokens}
    if m.get("temperature", True):
        body["temperature"] = temperature
    if is_deepseek(cfg):
        body["thinking"] = {"type": "disabled"}          # thinking is on by default and bills hidden tokens
    if tools and uses_tools(cfg):
        body["tools"] = tools
    return body


def _headers(cfg):
    key = cfg.get("api_key") or "none"                    # local servers ignore the key but some clients insist on one
    h = {"Content-Type": "application/json", "Authorization": "Bearer " + key, "User-Agent": "Sentinel/1.0"}
    if cfg.get("provider") == "openrouter":
        h.update({"HTTP-Referer": "http://localhost:8765", "X-Title": "Sentinel"})
    return h


def post_chat(cfg, body, timeout=12):
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions", method="POST", headers=_headers(cfg), data=json.dumps(body, separators=(",", ":")).encode())
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def friendly(e):
    """Turn a raw exception into one plain sentence a beginner can act on."""
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        try:
            detail = json.loads(e.read()).get("error", {})
            detail = detail.get("message") if isinstance(detail, dict) else str(detail)
        except Exception:
            detail = ""
        base = {401: "The provider rejected the key. Check it was copied completely.", 403: "The key is not allowed to use this model or endpoint.",
                402: "The account is out of credit.", 404: "Model or URL not found. Pick another model or check the base URL.",
                429: "Rate-limited or out of quota. Wait a moment or check your plan.", 400: "The provider rejected the request (often a wrong model name)."}.get(code, "Provider error %d." % code)
        return (base + (" (" + detail[:90] + ")" if detail else "")).strip()
    if isinstance(e, urllib.error.URLError):
        return "Could not reach the server. " + ("Is it running (Ollama / LM Studio)?" if "localhost" in str(e) or "127.0.0.1" in str(e) else "Check your internet connection and the base URL.")
    return str(e)[:160]


def list_models(cfg, timeout=8):
    """Free call: ask the provider which models this key can use. Falls back to the built-in suggestions."""
    m = meta(cfg)
    try:
        if not cfg.get("base_url"):
            raise ValueError("no base url")
        req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/models", headers=_headers(cfg))
        d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        ids = [str(x.get("id", "")).replace("models/", "") for x in (d.get("data") or d.get("models") or []) if isinstance(x, dict)]
        ids = sorted({i for i in ids if i and not any(w in i.lower() for w in ("embed", "whisper", "tts", "image", "moderation", "dall"))})
        if ids:
            return dict(models=ids[:150], live=True, error="")
    except Exception as e:
        return dict(models=list(m.get("models", [])), live=False, error=friendly(e))
    return dict(models=list(m.get("models", [])), live=False, error="")


def ask(cfg, prompt, max_tokens=60):
    """Simple one-shot call used by the connection test and the optional brief. Returns (text, tokens_in, tokens_out)."""
    if cfg.get("provider") == "claude_cli":
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError("The Claude CLI was not found on this PC.")
        out = subprocess.run([exe, "-p", "--model", cfg.get("model") or "haiku", prompt], capture_output=True, text=True, timeout=max(cfg.get("timeout", 12), 30))
        if out.returncode:
            raise RuntimeError(out.stderr[:200])
        return out.stdout, len(prompt) // 4, len(out.stdout) // 4
    if not configured(cfg):
        raise RuntimeError("Add an API key first." if meta(cfg).get("needs_key", True) else "Add the server address first.")
    try:
        d = post_chat(cfg, build_body(cfg, [{"role": "user", "content": prompt}], max_tokens=max_tokens), cfg.get("timeout", 12))
    except Exception as e:
        raise RuntimeError(friendly(e))
    u = d.get("usage", {})
    return d["choices"][0]["message"].get("content") or "", u.get("prompt_tokens", len(prompt) // 4), u.get("completion_tokens", 20)
