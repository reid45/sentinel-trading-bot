"""Optional phone/desktop alerts: Discord, Slack, Telegram or any JSON webhook. Fire-and-forget on a background thread with a short
timeout, so a slow or broken webhook can never delay a trade. Secrets (webhook URL, bot token) live only in data/config.json."""
import json, threading, urllib.request

TITLES = {"trade": "Trade opened", "close": "Trade closed", "error": "Alert"}
FLAG = {"trade": "on_open", "close": "on_close", "error": "on_alert"}


def _post(url, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json", "User-Agent": "Sentinel"})
    urllib.request.urlopen(req, timeout=6).read()


def build(cfg, title, text):
    """-> (url, payload) for the configured service, or None when nothing is set up."""
    kind = cfg.get("kind", "discord")
    if kind == "telegram":
        if not (cfg.get("telegram_token") and cfg.get("telegram_chat")):
            return None
        return "https://api.telegram.org/bot%s/sendMessage" % cfg["telegram_token"], {"chat_id": cfg["telegram_chat"], "text": "%s\n%s" % (title, text)}
    url = cfg.get("url") or ""
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return None
    if kind == "discord":
        return url, {"content": "**%s**\n%s" % (title, text)}
    if kind == "slack":
        return url, {"text": "*%s*\n%s" % (title, text)}
    return url, {"title": title, "text": text, "source": "sentinel"}


def send(cfg, title, text, wait=False):
    """Returns None (fire-and-forget) or, with wait=True, (ok, error) - used by the Test button."""
    b = build(cfg, title, text)
    if not b:
        return (False, "set up a webhook URL (https) or Telegram token + chat id first") if wait else None
    if not wait:
        threading.Thread(target=lambda: _quiet(b), daemon=True).start()
        return None
    try:
        _post(*b)
        return True, ""
    except Exception as e:
        return False, str(e)[:160]


def _quiet(b):
    try:
        _post(*b)
    except Exception:
        pass


def on_event(cfg, level, msg):
    n = cfg.get("notify") or {}
    if level in FLAG and n.get(FLAG[level]):
        send(n, TITLES[level], msg)
