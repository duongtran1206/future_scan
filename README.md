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
- Optional `systemd` service mode for auto-start on reboot.

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

## Command modes

Main script modes in `main.py`:

- Default (interval loop):
```bash
python main.py
```

- One cycle only:
```bash
python main.py --once
```

- Custom interval:
```bash
python main.py --interval-minutes 15
```

- Limit number of coins captured:
```bash
python main.py --max-coins 10
```

- Debug without Telegram send:
```bash
python main.py --once --skip-telegram --keep-images
```

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
- Captures are written to `captures/YYYY-MM-DD/` before sending.
- After successful send, images are deleted unless `--keep-images` is used.
