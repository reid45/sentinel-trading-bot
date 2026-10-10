# Putting Sentinel on a website (subdomain)

This guide takes Sentinel from "runs on one computer" to `https://sentineltrade.axiominternational.ca`, protected by a login.
It assumes a small Linux server (Ubuntu 22.04 or newer, 1 GB of RAM is plenty). No databases or packages to install: Sentinel is plain Python.

> **Read this first.** Sentinel starts in **practice mode** (real prices, simulated money). Keep it that way on a public site until you have
> watched it for weeks. Sentinel can place real orders only if an owner turns on Exchange mode and adds API keys; nothing here does that for you.

## 1. Where to host it (important)

The bot needs live prices from Binance (backup: Bybit). Both block some countries and cloud regions.
Before you rent a server, rent it in a region that works, then run the built-in check on it:

```bash
python3 sentinel.py doctor
```

Every line should say `[ OK ]`. A `HTTP 451` means that region is blocked: choose another data centre (any large provider's US, UK or EU region usually works).
From an Ontario home or office connection, Binance and Bybit both answered during development.

## 2. DNS

At the DNS provider for `axiominternational.ca`, add one record for the subdomain, pointing at the server:

| Type | Name | Value |
|------|------|-------|
| A | `sentineltrade` | the server's IPv4 address |
| AAAA (optional) | `sentineltrade` | the server's IPv6 address |

(If the server is behind another host name, use a `CNAME` to that name instead.) DNS can take a few minutes to spread.

## 3. Install

```bash
sudo apt update && sudo apt install -y python3 git
sudo useradd --system --create-home --shell /usr/sbin/nologin sentinel
sudo git clone https://github.com/reid45/sentinel-trading-bot.git /opt/sentinel
sudo chown -R sentinel:sentinel /opt/sentinel
sudo mkdir -p /var/lib/sentinel && sudo chown sentinel:sentinel /var/lib/sentinel
```

Run the service with systemd (it restarts by itself and starts on boot):

```bash
sudo cp /opt/sentinel/deploy/sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sentinel
```

The unit file already contains these settings (edit `SENTINEL_HOSTS` if your name differs):

| Setting | Meaning |
|---------|---------|
| `SENTINEL_HOSTS=sentineltrade.axiominternational.ca` | The only website name it answers to. Setting it also forces sign-in on. |
| `SENTINEL_TRUST_PROXY=1` | Believe the visitor address and https flag that Caddy/nginx add. |
| `HOST=127.0.0.1` | Listen on this machine only. The web server in front is the only way in. |
| `TRADEBOT_DATA=/var/lib/sentinel` | Where the database, settings (including keys) and downloaded candles live. **Back this folder up.** |

**Docker instead?** `docker compose up -d --build` does the same thing (see `docker-compose.yml`).

## 4. HTTPS in front (pick one)

**Caddy** (simplest, certificate is automatic):

```bash
sudo apt install -y caddy
sudo cp /opt/sentinel/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

**nginx**: use `deploy/nginx.conf`, get the certificate with `sudo certbot --nginx -d sentineltrade.axiominternational.ca`.
It also rate-limits the sign-in endpoint.

Open ports 80 and 443 in the server's firewall (`sudo ufw allow 80,443/tcp && sudo ufw allow OpenSSH && sudo ufw enable`). **Do not open 8765.**

## 5. First sign-in (creating the owner account)

The first account is protected so a stranger cannot claim your site. Get the one-time setup code:

```bash
sudo journalctl -u sentinel -n 20 | grep "setup"      # or:  sudo cat /var/lib/sentinel/setup_code.txt
```

Open `https://sentineltrade.axiominternational.ca`, choose a username and a password (10+ characters, a few unrelated words is ideal),
and paste the code. After that the code is deleted. The **owner** can then add **view-only** accounts under Settings, Sign-in and accounts.

## 6. How it protects itself

* Passwords are stored as PBKDF2-SHA256 (600,000 rounds) with a random salt. Sessions are random tokens in a `HttpOnly`, `SameSite=Lax`, `Secure` cookie; only a hash of each token is stored.
* 5 wrong passwords lock that address and that username for 15 minutes. Changing a password signs out every device.
* Every write needs the sign-in cookie **and** a custom header, and a matching `Origin`, so other websites cannot make your browser trade.
* Unknown `Host` names are refused (blocks DNS tricks). Security headers are sent on every response.
* View-only accounts cannot trade, change settings, read keys or spend AI money.
* The TradingView webhook (`/hook`) is the one open door; it needs its own long secret (Settings).

## 7. Everyday operations

| Task | Command |
|------|---------|
| Is it healthy? | `curl https://sentineltrade.axiominternational.ca/healthz` (public, returns `{"ok": true}`), or the **Health** chip in the app |
| Logs | `sudo journalctl -u sentinel -f` |
| Update | `cd /opt/sentinel && sudo -u sentinel git pull && sudo systemctl restart sentinel` |
| Back up | copy `/var/lib/sentinel/bot.db` and `config.json` (keys are inside `config.json`: keep the backup private) |
| Forgot the owner password | `sudo -u sentinel TRADEBOT_DATA=/var/lib/sentinel python3 /opt/sentinel/sentinel.py passwd <username>` |
| Locked out of sign-in entirely | `sentinel.py auth-off` only works when sign-in is not forced by `SENTINEL_HOSTS`; remove that line, restart, fix, put it back |
| Alerts to a phone | Settings, Alerts: Discord / Slack / Telegram webhook |

## 8. Going further (optional)

* **Several people**: add view-only accounts for clients or teammates; only owners can trade.
* **Branding**: Settings, Appearance: app name, accent colour, light/dark theme. These also style the sign-in page.
* **Real money**: do not. Keep **Practice mode**. If you ever connect an exchange, start with the exchange's demo/testnet and tiny size, and read the README's risk section first.

## Checklist before telling anyone the link

- [ ] `python3 sentinel.py doctor` is all green on the server
- [ ] Site opens on `https://`, the padlock is shown, `http://` redirects
- [ ] Owner account created; the setup code file is gone
- [ ] `curl -I http://SERVER_IP:8765` from outside **fails** (port closed)
- [ ] Health chip says **All good**
- [ ] Backup of `/var/lib/sentinel` scheduled
