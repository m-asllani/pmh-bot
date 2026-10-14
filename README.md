# PMH Bot

Automates this strategy:

> Mark the premarket high (PMH) and premarket low. Add VWAP.
> If the stock goes over PMH and VWAP is under PMH, trade the PMH retest.
> If VWAP is over PMH, trade the VWAP retest. Sell at high of day.

## What it does each trading day (times in New York time)

| ET    | Your clock (CET/CEST)* | What happens |
|-------|------------------------|--------------|
| 08:00, 08:45 | 14:00, 14:45 | **Bulls & bears report**: biggest gaps up (bulls) and down (bears) with PMH / PML, flagging **new premarket highs/lows** since the last report |
| 09:15 | 15:15 | Final report. The bulls (plus your watchlist) become the stocks it monitors after the open |
| 09:30 | 15:30 | Re-checks the final PMH / PML at the open and sends them again |
| 09:31 to 12:00 | 15:31 to 18:00 | Checks every 1‑minute candle and alerts you on: |
| | | **Broke PMH**: heads-up, says whether it's a PMH or VWAP retest plan |
| | | **BUY**: candle dipped to the level (PMH, or VWAP if VWAP > PMH) and closed green back above it. Includes entry, stop and target (high of day) |
| | | **SELL**: price reached the high of day target |
| | | **STOP**: the retest failed |
| 12:00 | 18:00 | Session summary |

\* The bot converts times automatically, including the weeks when US and EU daylight saving don't line up.
Weekends and US market holidays are skipped.

## Setup (already done once)

```bash
./setup.sh          # venv + dependencies + your private ntfy topic
./bot test-notify   # Mac banner + phone push
```

**Phone alerts:** install the free **ntfy** app (iOS/Android), tap **+**, and subscribe to the topic in
`config.yaml` → `notifications.ntfy_topic`. Telegram and Discord are also supported (see `config.yaml`).

**Email (easiest):** sign up free at [resend.com](https://resend.com) with the address you want alerts at,
create an API key, and set `resend_api_key` + `email_to` (in `config.local.yaml` or as GitHub secrets
`PMH_RESEND_API_KEY` / `PMH_EMAIL_TO`). Without your own domain, Resend only delivers to your sign-up address.

**Email via SMTP:** works with any SMTP account. With Gmail, turn on 2-step verification and create an
[App Password](https://myaccount.google.com/apppasswords). Put `email_to`, `smtp_user` and `smtp_password` in
`config.local.yaml` (private, never committed). Set `email_reports_only: true` to get only the watchlist and
summary by email and the live BUY/SELL alerts as phone pushes, which arrive faster.

## Runs in the cloud every trading day (GitHub Actions)

`.github/workflows/pmh-bot.yml` starts the bot at 7:45 ET every weekday on GitHub's servers, so your laptop can be
off. It sends the 8:00 / 8:45 / 9:15 reports, monitors until 12:00 and exits. Holidays are skipped automatically.

Notification settings come from the repo's **Settings → Secrets and variables → Actions**:

```bash
gh secret set PMH_NTFY_TOPIC      # phone push
gh secret set PMH_EMAIL_TO        # email (optional)
gh secret set PMH_SMTP_USER
gh secret set PMH_SMTP_PASSWORD
```

Run it by hand from the **Actions** tab ("Run workflow"). For example, `test-notify`, or `replay --date 2026-10-13`
to check the data from GitHub's servers. Each run's logs are saved as an artifact for 90 days.

## Running it locally

```bash
./bot run                  # runs every trading day, leave it open
./install_autostart.sh     # or: run in the background and start at every login
./install_autostart.sh --remove

./bot now                  # start today's session immediately (scan now, then monitor)
./bot scan                 # one-off premarket scan, printed
./bot replay               # simulate the latest trading day: what would it have sent?
./bot replay --date 2026-10-13
./bot replay --date 2026-10-13 --symbols TCRT AEHL
```

The Mac has to be **awake** at scan time. While a session runs the bot keeps the Mac awake on its own.
To wake it automatically each weekday: `sudo pmset repeat wakeorpoweron MTWRF 15:10:00` (local time).

## Tuning

Everything is in `config.yaml`: scan time, how long to monitor, gap %, price range, how many stocks,
retest tolerance, green-candle confirmation, stop buffer, and which alerts to send.
Add tickers you always want watched to `watchlist.txt`.

Every signal is logged to `logs/signals.csv`; the full log is `logs/pmh_bot.log`.

## Good to know

- **Data** comes from Yahoo Finance: free, no key, with real-time 1‑minute candles including premarket.
  Yahoo reports **no premarket volume**, so:
  - Liquidity is filtered by how many premarket minutes actually traded (`min_premarket_active_minutes`).
  - **VWAP is calculated from the 9:30 open.** That's standard session VWAP, the same as TradingView with
    extended hours off. If you chart VWAP with extended hours on, your VWAP will differ a bit.
- Alerts arrive about 5 seconds after each 1‑minute candle closes. This is a scanner and alert tool, not
  an auto-trader. It never places orders.
- Not financial advice. Run `./bot replay` on a few past days to see how the rules behave before risking money.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -t .
```
