# future_scan

Auto workflow for Binance Futures on Ubuntu:
1. Open futures market list.
2. Sort by `24h Chg` descending.
3. Capture only `1H` charts for volatile coins in that list.
4. Send all captured charts to Telegram.
5. Delete sent images to save disk.

## Features

- Default run interval: every 10 minutes.
- Capture only volatile-list coins (no unrelated symbols).
- File names use symbol names (example: `BTCUSDT.png`).
- Telegram delivery per cycle.
- Auto cleanup of sent images.
- Clears previously sent Telegram messages before each new cycle (the chat only ever shows the latest list).
- Optional `systemd` service mode for auto-start on reboot.

## How it works

Each cycle performs these steps:

1. **Discover the volatile list** — opens the Binance Futures page (seeded with `BTCUSDT`, falling back to `ETHUSDT` / `BNBUSDT`) and opens the coin list panel.
2. **Sort by 24h change** — clicks the `24h Chg` column header until the list is sorted descending (top gainers first).
3. **Extract coin rows** — scrolls the panel and collects up to `DEFAULT_VOLATILE_LIST_LIMIT` (20) USDT futures pairs with their 24h change, then saves them to `captures/YYYY-MM-DD/volatile_coin_list.json`.
4. **Capture 1H charts** — opens each coin's futures page, switches the chart to fullscreen and the `1H` timeframe, then screenshots the chart canvas as `SYMBOL.png`.
5. **Send to Telegram** — uploads each chart with a caption (`SYMBOL | 1H chart | 24h Chg: X%`).
6. **Clean up** — deletes sent images to save disk.

If any step fails, an error report (timestamp + context + traceback) is sent to Telegram.

> The bot tracks the `message_id`s it sends in `run/telegram_messages.json`. At the start of each new cycle it deletes those previous messages via the Telegram `deleteMessage` API, so the chat only ever contains the latest list.

## Repository structure

- `main.py`: main application.
- `requirements.txt`: Python dependencies.
- `.env.example`: environment variable template.
- `scripts/install_ubuntu.sh`: one-shot Ubuntu installation script.
- `scripts/run_server.sh`: start script (loads `.env`, runs app).
- `scripts/daemon.sh`: detached background runner (survives SSH logout).
- `systemd/future-scan.service`: sample service unit.

## Quick start on Ubuntu (pull and run)

### 1. Pull repo

```bash
git clone <YOUR_GIT_REPO_URL> future_scan
cd future_scan
```

### 2. Install everything

```bash
chmod +x scripts/install_ubuntu.sh scripts/run_server.sh
./scripts/install_ubuntu.sh
```

### 3. Configure Telegram

```bash
cp .env.example .env
nano .env
```

Set at least:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional:
- `INTERVAL_MINUTES` (default `10`)
- `MAX_COINS` (default `0`, means all coins from volatile list)

### 4. Run

```bash
./scripts/run_server.sh
```

That is enough to run from one repo on Ubuntu.

## Run in background (keep running after SSH logout)

Use the daemon helper script:

```bash
chmod +x scripts/daemon.sh scripts/run_server.sh scripts/install_ubuntu.sh
./scripts/daemon.sh start
```

Useful commands:

```bash
./scripts/daemon.sh status
./scripts/daemon.sh logs
./scripts/daemon.sh stop
./scripts/daemon.sh restart
```

The process runs with `nohup` and keeps running after you close SSH.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes (if sending) | — | Telegram bot token from @BotFather. |
| `TELEGRAM_CHAT_ID` | Yes (if sending) | — | Target Telegram chat / channel id. |
| `INTERVAL_MINUTES` | No | `10` | Interval between cycles (used by `scripts/run_server.sh`). |
| `MAX_COINS` | No | `0` | Max volatile-list coins to capture; `0` = all. |

Any of these can be overridden per run with the matching CLI flag (`--telegram-bot-token`, `--telegram-chat-id`, `--interval-minutes`, `--max-coins`).

## Command modes

Main script modes in `main.py`:

- Default (interval loop, every `--interval-minutes` minutes):
```bash
python main.py
```

- One cycle only:
```bash
python main.py --once
```

- Daily run at a fixed time:
```bash
python main.py --daily --time 09:00
```

- Custom interval:
```bash
python main.py --interval-minutes 15
```

- Limit number of coins captured:
```bash
python main.py --max-coins 10
```

- Change output directory:
```bash
python main.py --output-dir /path/to/captures
```

- Debug without Telegram send (keeps images, visible browser):
```bash
python main.py --once --skip-telegram --keep-images --headed
```

### Full CLI reference

| Flag | Description | Default |
| --- | --- | --- |
| `--daily` | Run every day at `--time`. | off |
| `--time HH:MM` | Daily run time (24h format). | `09:00` |
| `--once` | Run one cycle and exit. | off |
| `--interval-minutes N` | Interval between cycles in minutes. | `10` |
| `--max-coins N` | Max volatile-list coins to capture; `0` = all. | `0` |
| `--output-dir DIR` | Directory for screenshots. | `captures` |
| `--headed` | Run browser in visible mode (debugging). | off (headless) |
| `--skip-telegram` | Skip sending images to Telegram. | off |
| `--keep-images` | Keep images after each cycle (no cleanup). | off |
| `--telegram-bot-token` | Telegram bot token (overrides env). | env value |
| `--telegram-chat-id` | Telegram chat id (overrides env). | env value |

> Note: without `--once` or `--daily`, the script runs the interval loop.

## Run as systemd service

1. Edit service file paths/user if needed:
- `systemd/future-scan.service`

2. Install service:

```bash
sudo cp systemd/future-scan.service /etc/systemd/system/future-scan.service
sudo systemctl daemon-reload
sudo systemctl enable future-scan
sudo systemctl start future-scan
```

3. Check logs:

```bash
sudo systemctl status future-scan
journalctl -u future-scan -f
```

## Notes

- The app requires network access to Binance and Telegram APIs.
- If Telegram env values are missing and Telegram is enabled, the app exits with an explicit error.
- The volatile list collects up to 20 coins by default (see `DEFAULT_VOLATILE_LIST_LIMIT` in `main.py`); use `--max-coins` to lower it.
- Charts are captured in fullscreen on the `1H` timeframe.
- Captures are written to `captures/YYYY-MM-DD/` before sending; a per-day `volatile_coin_list.json` records the volatile list metadata.
- After successful send, images are deleted unless `--keep-images` is used.
- Running `python main.py` with no flags starts the interval loop; `--daily` and `--once` are the other run modes.
