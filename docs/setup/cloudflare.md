# Deploying ROaaS on Cloudflare (free plan) and connecting the lab

What runs where:

- **Our job API:** a Worker and a SQLite Durable Object, on **your** Cloudflare account.
- **The seller adapter:** on **Crow's** Cloudflare account, hosted by Rafa.
- **The lab agent:** on the mini PC, connecting out.

Nothing here costs money: Workers Free, no R2, test tokens only.

**Deployed on 2026-11-26 at `https://roaas.roaas-cloud.workers.dev`.** The two secrets were generated on the Mac
into `secrets/cloudflare.json` (git-ignored, readable only by the user) and uploaded with
`npx wrangler deploy --secrets-file ../secrets/cloudflare.json`. Move them to a password manager.

**Every HTTP client must send its own User-Agent.** Cloudflare answers 403 "error code: 1010" to Python's default
one (`Python-urllib/3.x`), on our Worker and on Crow's API alike. The `roaas` package sends
`roaas/<version>`; WebSocket clients are not affected.

## 1. Deploy the Worker (Mac, once)

```bash
cd ~/dev/roaas/cloud
npm install                                     # wrangler and TypeScript, inside this folder only
npx wrangler login                              # opens the browser: log in to your Cloudflare account
python3 -c "import secrets; print(secrets.token_urlsafe(32))"    # run twice: one ADAPTER_KEY, one LAB_TOKEN
npx wrangler secret put ADAPTER_KEY             # paste the first value
npx wrangler secret put LAB_TOKEN               # paste the second value
npx wrangler deploy                             # prints https://roaas.<your-subdomain>.workers.dev
```

- **Keep both values in a password manager.** Never put them in the repository or in a chat.
  - `ADAPTER_KEY` goes to Rafa over a private channel, for the hosted adapter.
  - `LAB_TOKEN` goes only to the mini PC.
- **Check:** `https://roaas.<your-subdomain>.workers.dev/api/status` answers `"online": false` until the lab
  agent connects.
- **Optional, operator emails:** `npx wrangler secret put RESEND_API_KEY` (a free resend.com key), and set
  `NOTIFY_TO` in `wrangler.jsonc`.

## 2. Prices and the Crow files (Mac)

1. Fill `crow/pricing.json`:
   - `token`: Crow Test USDC on Sepolia, `0xf1a4b68b201e9eddef8a054fb6a931b849d18504`;
   - the amounts, in base units with 6 decimals: `"20000000"` is 20.00.
2. Rebuild the files with the real URL:

```bash
cd ~/dev/roaas
PYTHONPATH=provider python3 -m roaas.cli offers build --fingerprint 0e811c503e02aa59faa288365e0f80023603ee9cfcd64f3ac4e67200960289af \
    --job-api https://roaas.<your-subdomain>.workers.dev --out crow
cd cloud && npx wrangler deploy                 # the Worker serves crow/pricing.json and crow/offers.json
```

Send Rafa `crow/seller.json` and `crow/template.json`, plus `ADAPTER_KEY` separately.

## 3. The lab agent (mini PC, Windows)

The agent reuses the kit's key, so the fingerprint stays `0e811c50…289af`, and the kit's private
`robot_info_cr3a.json`.

```powershell
# once: a copy of the repository's provider/ folder in C:\roaas\provider, then
cd C:\roaas\provider
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e ".[agent]"

# each session (PowerShell):
Set-PSReadLineOption -HistorySaveStyle SaveNothing
$env:ROAAS_LAB_TOKEN = "<LAB_TOKEN>"
$env:DOBOT_SERIAL_SALT = "<the lab's serial salt>"
roaas agent --cloud https://roaas.<your-subdomain>.workers.dev --robot-info C:\dobot_kit\robot_info_cr3a.json `
    --host 192.168.5.1 --live --keys-dir C:\dobot_kit\keys --out C:\roaas\episodes
```

- **Crow:** the agent reads each deal's terms from Crow's operators (`api.crow.direct`, `api2.crow.direct`,
  option `--crow-operators`), so the mini PC must reach them over the Wi-Fi.
- **Ready:** the agent prints `connected to wss://…/api/lab/ws`. `/api/status` then shows `"accepting": true`
  when the robot is disabled (mode 4), in TCP mode, without alarms, and DobotStudio Pro is closed.
- **Each funded job prints its program and asks for `ENABLE`.** Type it only with the emergency stop within
  reach. Ctrl+C stops the agent, and the cloud then refuses new quotes.
- **The kit in `C:\dobot_kit` is not modified.** Crow's pilot bridge keeps working.

## 4. A rehearsal before the first live deal

- **On the Mac, a full deal on Crow's local simulator** (fake robot, local chain):
  `python scripts/crow_sim_rehearsal.py <built Crow clone> . <empty folder> <python>` must end
  `"kind":"released"`.
- **On testnet with the fake robot,** Rafa deploys `crow/rehearsal/seller.json` and `template.json`
  (simulator-only terms), and the agent runs against `fake_dobot.py` on the mini PC (`--host 127.0.0.1`,
  without `--live`, and `--keys-dir C:\dobot_kit\keys` so the fingerprint matches the rehearsal terms). The
  agent declines production terms on the simulator before asking anyone, and Crow's checks would refuse such a
  run anyway.
