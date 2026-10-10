# Putting Sentinel on `sentineltrade.axiominternational.ca` with Cloudflare

> **Why not Cloudflare Pages?** Pages (and Workers) host static files and short serverless functions. Sentinel is a Python program that must run
> **all day**, keep a database and place trades in a loop, which Pages cannot do. What Cloudflare *does* offer for this is **Cloudflare Tunnel**:
> Sentinel runs on a small always-on machine, and the tunnel publishes it at your subdomain with free https, DDoS protection and **no open ports**.
> You still get the Cloudflare dashboard, DNS, WAF and (optionally) Cloudflare Access login in front of it.

```
 Browser ──https──▶ Cloudflare (your domain) ──encrypted tunnel──▶ cloudflared ──▶ Sentinel on 127.0.0.1:8765
```

You need: the domain on Cloudflare (nameservers already pointed there), a Cloudflare account that can open **Zero Trust**, and one always-on machine
(a $5 to $6/month Ubuntu VPS, a mini PC or a Raspberry Pi; a Windows PC works too but the site is only up while it is on).
Cost: Cloudflare Tunnel and the Zero Trust free plan (up to 50 users) are free.

## 1. Pick the machine and check it can see the markets

Binance and Bybit block some countries and data-centre regions. On the machine you chose:

```bash
git clone https://github.com/reid45/sentinel-trading-bot.git /opt/sentinel
cd /opt/sentinel && python3 sentinel.py doctor
```

Every line should say `[ OK ]`. A `451` means that region is blocked: choose another data centre. (From an Ontario connection both worked during development.)

## 2. Run Sentinel as a service on that machine

Linux (systemd), exactly as in [DEPLOY.md](DEPLOY.md) section 3, with these settings in `/etc/systemd/system/sentinel.service` (already in `deploy/sentinel.service`):

```ini
Environment=HOST=127.0.0.1                 # only the tunnel on this machine can reach it
Environment=SENTINEL_HOSTS=sentineltrade.axiominternational.ca
Environment=SENTINEL_TRUST_PROXY=1         # believe Cloudflare's visitor address and https flag
Environment=TRADEBOT_DATA=/var/lib/sentinel
```

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin sentinel
sudo chown -R sentinel:sentinel /opt/sentinel && sudo mkdir -p /var/lib/sentinel && sudo chown sentinel:sentinel /var/lib/sentinel
sudo cp /opt/sentinel/deploy/sentinel.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now sentinel
curl http://127.0.0.1:8765/healthz        # {"ok": true}
```

Windows: open PowerShell in the Sentinel folder and run  
`$env:SENTINEL_HOSTS="sentineltrade.axiominternational.ca"; $env:SENTINEL_TRUST_PROXY="1"; python server.py`  
(or use the Task Scheduler / NSSM to start it at boot).

## 3. Create the tunnel (all in the Cloudflare dashboard)

1. Cloudflare dashboard, left menu **Zero Trust** (first time: pick a team name and the Free plan).
2. **Networks, Tunnels, Create a tunnel**, choose **Cloudflared**, name it `sentinel`, **Save tunnel**.
3. Choose your machine's operating system. Cloudflare shows an install command that contains a long token. **Copy and run it on the Sentinel machine**, for example on Ubuntu:
   ```bash
   # install cloudflared (Cloudflare shows the exact lines for your system), then:
   sudo cloudflared service install <THE-LONG-TOKEN>
   ```
   Windows: run the same line as `cloudflared.exe service install <THE-LONG-TOKEN>` in an Administrator PowerShell. The token is a password: do not post it anywhere.
4. The tunnel should show **Healthy** in the dashboard within a minute. Click **Next**.
5. **Public Hostname** tab, add a hostname:

   | Field | Value |
   |---|---|
   | Subdomain | `sentineltrade` |
   | Domain | `axiominternational.ca` |
   | Path | *(leave empty)* |
   | Service type | `HTTP` |
   | URL | `localhost:8765` |

   Save. Cloudflare creates the DNS record (a proxied CNAME to the tunnel) for you. **You do not add any A record or open any firewall port.**

## 4. First sign-in (create the owner)

Open `https://sentineltrade.axiominternational.ca`. It shows the sign-in page in first-run mode. Get the one-time code on the Sentinel machine:

```bash
sudo journalctl -u sentinel -n 30 | grep -i setup       # or:  sudo cat /var/lib/sentinel/setup_code.txt
```

Choose a username and a password (10+ characters), paste the code, done. The code is deleted after use. Add view-only people later in **Settings, Sign-in and accounts**.

## 5. Recommended Cloudflare settings (5 minutes)

| Where | Setting | Why |
|---|---|---|
| **SSL/TLS, Overview** | Mode **Full** | Tunnel traffic is already encrypted; this keeps the browser side strict |
| **SSL/TLS, Edge Certificates** | **Always Use HTTPS** on, minimum TLS **1.2**, HSTS on (after you have confirmed https works) | Passwords never cross the internet unencrypted |
| **Security, WAF, Rate limiting rules** | New rule: URI path equals `/api/auth/login`, **10 requests per minute per IP**, action **Block** for 10 minutes | A second brake on password guessing (Sentinel also locks after 5 failures) |
| **Security, Bots** | Bot Fight Mode **on** | Cuts junk traffic |
| **Caching, Cache Rules** | If URI path starts with `/api/` then **Bypass cache** | Live data must never be cached (Sentinel already sends `no-store`; this is belt and braces) |

## 6. Optional but strongly advised: a Cloudflare Access gate in front

Sentinel has its own login. Adding **Cloudflare Access** puts a second lock in front, so strangers cannot even reach the sign-in page.

1. **Zero Trust, Access, Applications, Add an application, Self-hosted**.
2. Application name `Sentinel`, domain `sentineltrade` . `axiominternational.ca`, session 24 hours.
3. Policy **Allow**, rule: *Emails* equal to yours and your cousin's. Login method: One-time PIN (email) is enough.
4. **If you use the TradingView webhook:** add a second application for the path `sentineltrade.axiominternational.ca/hook` with a **Bypass** policy (everyone), because TradingView cannot sign in. The webhook has its own long secret. Do the same for `/healthz` if you want an uptime monitor.

## 7. Everyday use

| Task | How |
|---|---|
| Is it up? | `https://sentineltrade.axiominternational.ca/healthz` should return `{"ok": true}`; Zero Trust, Tunnels shows Healthy; the **Health** chip inside the app |
| Update Sentinel | `cd /opt/sentinel && sudo -u sentinel git pull && sudo systemctl restart sentinel` |
| Logs | `sudo journalctl -u sentinel -f` and `sudo journalctl -u cloudflared -f` |
| Backup | copy `/var/lib/sentinel/bot.db` and `config.json` (the config holds your keys: keep backups private) |
| Forgot owner password | `sudo -u sentinel TRADEBOT_DATA=/var/lib/sentinel python3 /opt/sentinel/sentinel.py passwd <username>` |
| Take the site offline | Zero Trust, Tunnels, `sentinel`, **Configure**, remove the public hostname (or stop `cloudflared`) |

## 8. Troubleshooting

| You see | Cause and fix |
|---|---|
| Cloudflare error **1033** or **502** | The tunnel is down or Sentinel is not running. `systemctl status cloudflared sentinel`; `curl http://127.0.0.1:8765/healthz` on the machine |
| "unknown host" (HTTP 421) | `SENTINEL_HOSTS` does not exactly match the address in the browser. Fix the spelling, restart Sentinel |
| Sign-in works but you are signed out on the next click | The browser blocks the cookie: make sure you are on `https://` (Sentinel only sets the `Secure` cookie flag when Cloudflare reports https; `SENTINEL_TRUST_PROXY=1` must be set) |
| Everyone shows the same IP / one visitor locks everyone out | `SENTINEL_TRUST_PROXY=1` missing, so Sentinel sees only the tunnel's address |
| Prices missing, red items in the Health chip | Run `python3 sentinel.py doctor` on the machine: probably Binance/Bybit blocked in that data centre |

## Checklist before you share the link

- [ ] `sentinel.py doctor` all green on the host machine
- [ ] Tunnel **Healthy**, site opens on `https://`, padlock shown
- [ ] Owner account created and the setup code file is gone
- [ ] Practice mode is on (Sentinel starts there); nothing connected to a real exchange
- [ ] Rate-limit rule and (recommended) Access policy added
- [ ] Backup of `/var/lib/sentinel` scheduled
