import argparse
import datetime as dt
import json
import os
import traceback
import time
from pathlib import Path

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

TICKER_24H_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
DEFAULT_OUTPUT_DIR = "captures"
DISCOVERY_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
DEFAULT_VOLATILE_LIST_LIMIT = 20
TELEGRAM_STATE_FILE = Path("run") / "telegram_messages.json"


def get_most_volatile_coin():
    """Return (symbol, volatility_pct) for the most volatile USDT futures pair."""
    response = requests.get(TICKER_24H_URL, timeout=20)
    response.raise_for_status()
    payload = response.json()

    max_volatility = -1.0
    top_symbol = None

    for item in payload:
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue

        try:
            high = float(item["highPrice"])
            low = float(item["lowPrice"])
        except (KeyError, TypeError, ValueError):
            continue

        if low <= 0:
            continue

        volatility = ((high - low) / low) * 100.0
        if volatility > max_volatility:
            max_volatility = volatility
            top_symbol = symbol

    if top_symbol is None:
        raise RuntimeError("No eligible USDT futures symbol found from Binance ticker data.")

    return top_symbol, max_volatility


def build_capture_folder(output_dir):
    day_folder = dt.datetime.now().strftime("%Y-%m-%d")
    folder = Path(output_dir) / day_folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_coin_chart_path(symbol, output_dir):
    folder = build_capture_folder(output_dir)
    return folder / f"{symbol}.png"


def build_coin_list_path(output_dir):
    folder = build_capture_folder(output_dir)
    return folder / "volatile_coin_list.json"


def sort_coin_list_desc_by_24h_change(page):
    # Header text as provided by user: "24h Chg".
    headers = page.locator("xpath=//*[normalize-space()='24h Chg']")
    if headers.count() == 0:
        return False

    def read_panel_changes():
        return page.evaluate(
            r"""
            () => {
                const headerNode = Array.from(document.querySelectorAll('*')).find(
                    (el) => (el.textContent || '').trim() === '24h Chg'
                );
                if (!headerNode) return [];

                let root = document;
                let p = headerNode;
                while (p) {
                    const linksInNode = p.querySelectorAll?.("a[href*='/en/futures/']")?.length || 0;
                    if (linksInNode >= 10) { root = p; break; }
                    p = p.parentElement;
                }

                const links = Array.from(root.querySelectorAll("a[href*='/en/futures/']"));
                const values = [];
                for (const link of links) {
                    const text = (link.innerText || '').replace(/\s+/g, ' ').trim();
                    if (!/USDT/.test(text) || !/%/.test(text)) continue;
                    const m = text.match(/([+-]?\d+(?:\.\d+)?)%/);
                    if (m) values.push(Number(m[1]));
                    if (values.length >= 10) break;
                }
                return values;
            }
            """
        )

    def is_descending(values):
        if len(values) < 2:
            return False
        descending_count = sum(
            1 for i in range(len(values) - 1) if values[i] >= values[i + 1]
        )
        return descending_count >= len(values) * 0.7 and values[0] > values[-1]

    # Try up to 5 clicks: first click activates sort, second toggles asc/desc, etc.
    for _ in range(5):
        clicked = False
        for i in range(min(headers.count(), 3)):
            try:
                headers.nth(i).click(timeout=5000)
                clicked = True
                break
            except PlaywrightError:
                continue

        if not clicked:
            try:
                page.evaluate(
                    """
                    () => {
                        const el = Array.from(document.querySelectorAll('*')).find(
                            (el) => (el.textContent || '').trim() === '24h Chg'
                        );
                        if (el) { el.click(); return true; }
                        return false;
                    }
                    """
                )
            except PlaywrightError:
                pass

        page.wait_for_timeout(1200)
        values = read_panel_changes()
        if is_descending(values):
            print(f"Sort confirmed: top values={values[:5]}")
            return True

    return False


def open_coin_dropdown(page, symbol):
    dropdown_candidates = [
        f"xpath=//h1[contains(normalize-space(),'{symbol}')]",
        "xpath=//h1[contains(normalize-space(),'USDT')]",
        "xpath=//div[contains(@class,'items-start') and contains(normalize-space(.),'Perp') and .//*[name()='svg']]",
        "xpath=//*[self::button or self::div][.//*[name()='svg']/*[name()='path' and contains(@d,'M16.37 8.75H7.63a.75.75 0 00-.569 1.238')]]",
        "xpath=//*[self::button or self::div][.//*[name()='svg']/*[name()='path' and contains(@d,'M16.37 8.75H7.63')]]",
    ]

    for _ in range(3):
        for selector in dropdown_candidates:
            locator = page.locator(selector)
            if locator.count() == 0:
                continue
            try:
                locator.first.click(timeout=5000)
                if wait_for_coin_list_header(page, timeout_ms=7000):
                    return True
            except PlaywrightError:
                continue
        page.wait_for_timeout(600)

    # Fallback for dynamic layouts: click visible symbol/perp block in top area.
    try:
        clicked = bool(
            page.evaluate(
                                r"""
                (seedSymbol) => {
                  const nodes = Array.from(document.querySelectorAll('h1,div,span,button'));
                  const candidates = nodes.filter((el) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 20 || rect.height < 12) return false;
                    if (rect.top > 260) return false;
                                        const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
                    return text.includes('Perp') || text.includes(seedSymbol) || /[A-Z0-9]{3,}USDT/.test(text);
                  });
                  if (!candidates.length) return false;
                  candidates[0].click();
                  return true;
                }
                """,
                symbol,
            )
        )
        if clicked and wait_for_coin_list_header(page, timeout_ms=7000):
            return True
    except PlaywrightError:
        pass

    return False


def ensure_coin_list_panel_open(page):
    return page.locator("xpath=//*[normalize-space()='24h Chg']").count() > 0


def wait_for_coin_list_header(page, timeout_ms=7000):
    step_ms = 250
    attempts = max(1, timeout_ms // step_ms)
    for _ in range(attempts):
        if ensure_coin_list_panel_open(page):
            return True
        page.wait_for_timeout(step_ms)
    return False


def collect_max_coin_list(page, max_items=DEFAULT_VOLATILE_LIST_LIMIT):
    rows = page.evaluate(
        r"""
        async ({ maxItems }) => {
            const isCoinRow = (a) => {
                const text = (a.innerText || '').replace(/\s+/g, ' ').trim();
                const href = a.getAttribute('href') || '';
                return /\/en\/futures\//.test(href) && /USDT/.test(text) && /%/.test(text);
            };

            const parseRow = (a) => {
                const href = a.getAttribute('href') || '';
                const symbolMatch = href.match(/\/en\/futures\/([A-Z0-9]+)/);
                const symbol = symbolMatch ? symbolMatch[1] : '';
                const text = (a.innerText || '').replace(/\s+/g, ' ').trim();
                const chgMatch = text.match(/([+-]?\d+(?:\.\d+)?)%/);
                const chg = chgMatch ? Number(chgMatch[1]) : null;
                return {
                    symbol,
                    chg,
                    chgText: chgMatch ? `${chgMatch[1]}%` : '',
                    text,
                    href,
                };
            };

            const headerNode = Array.from(document.querySelectorAll('*')).find(
                (el) => (el.textContent || '').trim() === '24h Chg'
            );

            if (!headerNode) {
                return [];
            }

            let root = document;
            let p = headerNode;
            while (p) {
                const linksInNode = p.querySelectorAll?.("a[href*='/en/futures/']")?.length || 0;
                if (linksInNode >= 10) {
                    root = p;
                    break;
                }
                p = p.parentElement;
            }

            const allCandidates = Array.from(root.querySelectorAll("a[href*='/en/futures/']"));
            const firstRow = allCandidates.find(isCoinRow);
            if (!firstRow) {
                return [];
            }

            let scrollContainer = null;
            let parent = firstRow.parentElement;
            while (parent) {
                if (parent.scrollHeight > parent.clientHeight + 20) {
                    scrollContainer = parent;
                    break;
                }
                parent = parent.parentElement;
            }

            const seen = new Map();
            const collectFrom = (nodeRoot) => {
                const links = Array.from(nodeRoot.querySelectorAll("a[href*='/en/futures/']")).filter(isCoinRow);
                for (const link of links) {
                    const row = parseRow(link);
                    if (!row.symbol || row.chg === null) continue;
                    if (!seen.has(row.symbol)) {
                        seen.set(row.symbol, row);
                        if (seen.size >= maxItems) return true;
                    }
                }
                return false;
            };

            if (!scrollContainer) {
                collectFrom(root);
            } else {
                let unchangedRounds = 0;
                let prevCount = -1;
                for (let i = 0; i < 80; i++) {
                    const reachedMax = collectFrom(scrollContainer);
                    if (reachedMax) break;

                    const currentCount = seen.size;
                    if (currentCount === prevCount) unchangedRounds += 1;
                    else unchangedRounds = 0;
                    prevCount = currentCount;

                    if (unchangedRounds >= 4) break;

                    const oldTop = scrollContainer.scrollTop;
                    scrollContainer.scrollTop = Math.min(
                        oldTop + scrollContainer.clientHeight * 0.9,
                        scrollContainer.scrollHeight
                    );
                    await new Promise((r) => setTimeout(r, 120));

                    if (scrollContainer.scrollTop === oldTop) break;
                }
            }

            return Array.from(seen.values()).sort((a, b) => (b.chg ?? -Infinity) - (a.chg ?? -Infinity));
        }
        """,
        {"maxItems": max_items},
    )
    return rows


def save_coin_list(rows, output_dir):
    payload = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "count": len(rows),
        "coins": rows,
    }
    out_path = build_coin_list_path(output_dir)
    out_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    print(f"Coin list saved: {out_path}")
    return out_path


def _telegram_api_url(bot_token):
    return f"https://api.telegram.org/bot{bot_token}"


def load_sent_message_ids():
    """Load message IDs previously sent by the bot from the state file."""
    try:
        if TELEGRAM_STATE_FILE.exists():
            data = json.loads(TELEGRAM_STATE_FILE.read_text(encoding="utf-8"))
            return data.get("message_ids", [])
    except (OSError, ValueError):
        pass
    return []


def save_sent_message_ids(message_ids):
    """Persist the list of bot-sent message IDs to the state file."""
    try:
        TELEGRAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "count": len(message_ids),
            "message_ids": message_ids,
        }
        TELEGRAM_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Warning: could not save telegram message state: {exc}")


def add_sent_message_id(message_id):
    """Record a message ID so it can be cleaned up in a later cycle."""
    if not message_id:
        return
    ids = load_sent_message_ids()
    if message_id not in ids:
        ids.append(message_id)
        save_sent_message_ids(ids)


def clear_previous_telegram_messages(bot_token, chat_id):
    """Delete all messages the bot sent previously (keeps the chat clean)."""
    if not bot_token or not chat_id:
        return

    ids = load_sent_message_ids()
    if not ids:
        return

    remaining = []
    deleted = 0
    for message_id in ids:
        try:
            url = f"{_telegram_api_url(bot_token)}/deleteMessage"
            response = requests.post(
                url,
                json={"chat_id": chat_id, "message_id": message_id},
                timeout=20,
            )
            if response.ok:
                deleted += 1
            else:
                remaining.append(message_id)
        except Exception as exc:
            print(f"Telegram delete failed for message {message_id}: {exc}")
            remaining.append(message_id)

    save_sent_message_ids(remaining)
    print(
        f"Cleared previous Telegram messages: deleted={deleted}, remaining={len(remaining)}"
    )


def send_telegram_text(bot_token, chat_id, message):
    """Send a plain-text message to Telegram. Non-fatal on failure."""
    if not bot_token or not chat_id:
        return None

    message_id = None
    try:
        url = f"{_telegram_api_url(bot_token)}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        response = requests.post(url, json=payload, timeout=30)
        if response.ok:
            message_id = response.json().get("result", {}).get("message_id")
            add_sent_message_id(message_id)
        else:
            print(f"Telegram text send failed: HTTP {response.status_code} {response.text}")
    except Exception as exc:
        print(f"Telegram text send exception: {exc}")

    return message_id


def notify_telegram_error(bot_token, chat_id, context, error):
    """Send an error report to Telegram with timestamp and context."""
    now_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
    tb_short = "".join(tb_lines[-3:]) if len(tb_lines) > 3 else "".join(tb_lines)
    msg = (
        f"<b>⚠️ future_scan Error</b>\n"
        f"<b>Time:</b> {now_str}\n"
        f"<b>Context:</b> {context}\n"
        f"<b>Error:</b> <code>{error}</code>\n"
        f"<pre>{tb_short[:800]}</pre>"
    )
    send_telegram_text(bot_token, chat_id, msg)


def send_images_to_telegram(image_paths, coin_rows, bot_token, chat_id):
    if not bot_token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID for Telegram sending.")

    chg_map = {row.get("symbol", ""): row.get("chgText", "") for row in coin_rows}
    total = len(image_paths)
    sent = 0
    failed = []
    for idx, image_path in enumerate(image_paths, start=1):
        symbol = image_path.stem
        chg_text = chg_map.get(symbol, "")
        caption = f"{symbol} | 1H chart"
        if chg_text:
            caption += f" | 24h Chg: {chg_text}"

        url = f"{_telegram_api_url(bot_token)}/sendPhoto"
        try:
            with image_path.open("rb") as image_file:
                files = {"photo": image_file}
                data = {"chat_id": chat_id, "caption": caption}
                response = requests.post(url, data=data, files=files, timeout=60)

            if not response.ok:
                failed.append(f"{symbol}: HTTP {response.status_code} {response.text[:200]}")
                print(f"Telegram send failed for {symbol}: HTTP {response.status_code}")
                continue

            message_id = response.json().get("result", {}).get("message_id")
            add_sent_message_id(message_id)
            sent += 1
            print(f"Telegram sent [{idx}/{total}]: {image_path.name}")
        except Exception as exc:
            failed.append(f"{symbol}: {exc}")
            print(f"Telegram send exception for {symbol}: {exc}")

    if failed:
        notify_telegram_error(
            bot_token, chat_id,
            f"send_images_to_telegram ({sent}/{total} sent, {len(failed)} failed)",
            Exception("; ".join(failed)),
        )

    return sent, failed


def delete_files(file_paths):
    deleted = 0
    for path in file_paths:
        try:
            if path.exists():
                path.unlink()
                deleted += 1
        except OSError as exc:
            print(f"Warning: failed to delete {path}: {exc}")
    print(f"Deleted image files: {deleted}")


def set_chart_to_fullscreen_and_1h(page):
    fullscreen_selectors = [
        "xpath=//*[name()='svg' and contains(@class,'chart-fullscreen-icon')]",
        "xpath=//*[name()='path' and contains(@d,'M9.363 13.363a.9.9 0 011.274 1.274')]/ancestor::*[name()='svg'][1]",
    ]

    fullscreen_clicked = False
    for selector in fullscreen_selectors:
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        try:
            locator.first.click(timeout=5000)
            fullscreen_clicked = True
            break
        except PlaywrightError:
            continue

    if not fullscreen_clicked:
        print("Warning: could not click fullscreen chart button.")

    one_h_selectors = [
        "xpath=//*[normalize-space()='Time']/following::*[normalize-space()='1H'][1]",
        "xpath=//*[@id='1h' and normalize-space()='1H']",
        "xpath=//*[normalize-space()='1H' and contains(@class,'cursor-pointer')]",
        "xpath=//*[normalize-space()='1H']",
    ]

    one_h_clicked = False
    for selector in one_h_selectors:
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        try:
            locator.first.click(timeout=5000)
            one_h_clicked = True
            break
        except PlaywrightError:
            continue

    if not one_h_clicked:
        # Last-resort DOM click for dynamic components where role/tag changes.
        one_h_clicked = bool(
            page.evaluate(
                """
                () => {
                  const nodes = Array.from(document.querySelectorAll('div,span,button'));
                  const target = nodes.find((el) => {
                    const text = (el.textContent || '').trim();
                    const rect = el.getBoundingClientRect();
                    return text === '1H' && rect.width > 0 && rect.height > 0;
                  });
                  if (!target) return false;
                  target.click();
                  return true;
                }
                """
            )
        )

    if not one_h_clicked:
        # Some layouts already render 1H as selected and not clickable.
        one_h_clicked = page.locator("xpath=//*[normalize-space()='1H']").count() > 0

    if not one_h_clicked:
        print("Warning: could not click 1H timeframe button.")

    page.wait_for_timeout(1000)


def discover_volatile_coin_list(page, seed_symbol):
    attempts = [seed_symbol] if seed_symbol else []
    attempts.extend([s for s in DISCOVERY_SYMBOLS if s not in attempts])
    last_error = ""

    for candidate_symbol in attempts:
        try:
            seed_url = f"https://www.binance.com/en/futures/{candidate_symbol}"
            page.goto(seed_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("canvas", timeout=20000)
            page.wait_for_timeout(1200)

            if not open_coin_dropdown(page, candidate_symbol):
                raise RuntimeError("Could not open futures coin list.")

            if not ensure_coin_list_panel_open(page):
                raise RuntimeError("Coin list panel not detected (missing 24h Chg header).")

            sorted_ok = sort_coin_list_desc_by_24h_change(page)
            if not sorted_ok:
                raise RuntimeError("Could not click 24h Chg to sort descending.")

            rows = collect_max_coin_list(page, max_items=DEFAULT_VOLATILE_LIST_LIMIT)
            if not rows:
                raise RuntimeError("No volatile coin rows extracted from list.")

            print(f"Volatile list discovered from: {candidate_symbol}")
            return rows
        except Exception as exc:
            last_error = str(exc)
            continue

    raise RuntimeError(f"Failed to discover volatile coin list. Last error: {last_error}")


def capture_1h_chart_for_symbol(page, symbol, output_dir):
    url = f"https://www.binance.com/en/futures/{symbol}"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("canvas", timeout=20000)
    set_chart_to_fullscreen_and_1h(page)
    page.wait_for_timeout(1200)

    out_path = build_coin_chart_path(symbol, output_dir)
    canvas = page.locator("canvas").first
    if canvas.count() > 0:
        canvas.screenshot(path=str(out_path), timeout=15000)
    else:
        page.screenshot(path=str(out_path), full_page=False)

    print(f"Saved 1H chart: {out_path}")
    return out_path


def run_once(output_dir=DEFAULT_OUTPUT_DIR, headless=True, max_coins=0):
    symbol = DISCOVERY_SYMBOLS[0]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page(viewport={"width": 1920, "height": 1080})

            coin_rows = discover_volatile_coin_list(page, symbol)
            save_coin_list(coin_rows, output_dir)

            symbols = []
            seen = set()
            for row in coin_rows:
                item_symbol = row.get("symbol", "")
                if not item_symbol.endswith("USDT"):
                    continue
                if item_symbol in seen:
                    continue
                seen.add(item_symbol)
                symbols.append(item_symbol)

            if max_coins > 0:
                symbols = symbols[:max_coins]

            if not symbols:
                raise RuntimeError("Volatile list is empty. No charts captured.")

            print(f"Will capture only volatile list coins: {len(symbols)}")
            captured_paths = []
            for idx, item_symbol in enumerate(symbols, start=1):
                print(f"[{idx}/{len(symbols)}] Capturing 1H chart for {item_symbol}")
                captured_paths.append(capture_1h_chart_for_symbol(page, item_symbol, output_dir))

            browser.close()
            return captured_paths, coin_rows
    except PlaywrightError as exc:
        if "Executable doesn't exist" in str(exc):
            raise RuntimeError(
                "Playwright browser is missing. Run: python -m playwright install chromium"
            ) from exc
        raise


def run_cycle(
    output_dir=DEFAULT_OUTPUT_DIR,
    headless=True,
    max_coins=0,
    send_telegram=True,
    cleanup_images=True,
    bot_token=None,
    chat_id=None,
):
    if send_telegram:
        clear_previous_telegram_messages(bot_token, chat_id)

    try:
        image_paths, coin_rows = run_once(output_dir=output_dir, headless=headless, max_coins=max_coins)
    except Exception as exc:
        notify_telegram_error(bot_token, chat_id, "run_once (discovery + capture)", exc)
        raise

    try:
        if send_telegram:
            send_images_to_telegram(image_paths, coin_rows, bot_token=bot_token, chat_id=chat_id)
    except Exception as exc:
        notify_telegram_error(bot_token, chat_id, "send_images_to_telegram", exc)
        raise
    finally:
        if cleanup_images:
            delete_files(image_paths)


def seconds_until_next_run(target_hhmm):
    now = dt.datetime.now()
    hour, minute = map(int, target_hhmm.split(":"))
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += dt.timedelta(days=1)
    return int((next_run - now).total_seconds())


def validate_hhmm(value):
    try:
        hour, minute = map(int, value.split(":"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Time must be in HH:MM format.") from exc

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise argparse.ArgumentTypeError("Time must be valid 24h HH:MM (00:00-23:59).")

    return f"{hour:02d}:{minute:02d}"


def run_daily(
    target_time,
    output_dir=DEFAULT_OUTPUT_DIR,
    headless=True,
    max_coins=0,
    send_telegram=True,
    cleanup_images=True,
    bot_token=None,
    chat_id=None,
):
    print(f"Daily mode active. Script will run every day at {target_time}.")
    while True:
        wait_seconds = seconds_until_next_run(target_time)
        next_run_at = dt.datetime.now() + dt.timedelta(seconds=wait_seconds)
        print(f"Next run at: {next_run_at.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(wait_seconds)
        try:
            run_cycle(
                output_dir=output_dir,
                headless=headless,
                max_coins=max_coins,
                send_telegram=send_telegram,
                cleanup_images=cleanup_images,
                bot_token=bot_token,
                chat_id=chat_id,
            )
        except Exception as exc:  # Keep daily loop alive even if one run fails.
            notify_telegram_error(bot_token, chat_id, "run_daily cycle", exc)
            print(f"Run failed: {exc}")


def run_interval(
    interval_minutes=10,
    output_dir=DEFAULT_OUTPUT_DIR,
    headless=True,
    max_coins=0,
    send_telegram=True,
    cleanup_images=True,
    bot_token=None,
    chat_id=None,
):
    if interval_minutes <= 0:
        raise RuntimeError("--interval-minutes must be > 0")

    print(f"Interval mode active: every {interval_minutes} minutes")
    while True:
        started = dt.datetime.now()
        print(f"Cycle started at: {started.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            run_cycle(
                output_dir=output_dir,
                headless=headless,
                max_coins=max_coins,
                send_telegram=send_telegram,
                cleanup_images=cleanup_images,
                bot_token=bot_token,
                chat_id=chat_id,
            )
            print("Cycle done.")
        except Exception as exc:
            notify_telegram_error(bot_token, chat_id, f"run_interval ({interval_minutes}m cycle)", exc)
            print(f"Cycle failed: {exc}")

        next_at = dt.datetime.now() + dt.timedelta(minutes=interval_minutes)
        print(f"Next cycle at: {next_at.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(interval_minutes * 60)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Find top volatile Binance Futures USDT pair and capture chart screenshot."
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run every day at --time (HH:MM).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle and exit.",
    )
    parser.add_argument(
        "--time",
        type=validate_hhmm,
        default="09:00",
        help="Daily run time in HH:MM (24h). Default: 09:00",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for screenshots. Default: captures",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in visible mode for debugging.",
    )
    parser.add_argument(
        "--max-coins",
        type=int,
        default=0,
        help="Maximum volatile-list coins to capture. 0 means all in list.",
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=10,
        help="Run every N minutes in interval mode. Default: 10",
    )
    parser.add_argument(
        "--skip-telegram",
        action="store_true",
        help="Skip sending images to Telegram.",
    )
    parser.add_argument(
        "--keep-images",
        action="store_true",
        help="Keep images after each cycle (do not delete).",
    )
    parser.add_argument(
        "--telegram-bot-token",
        default="",
        help="Telegram bot token. Can also be set via TELEGRAM_BOT_TOKEN env.",
    )
    parser.add_argument(
        "--telegram-chat-id",
        default="",
        help="Telegram chat id. Can also be set via TELEGRAM_CHAT_ID env.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    is_headless = not args.headed
    bot_token = args.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = args.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    send_telegram = not args.skip_telegram
    cleanup_images = not args.keep_images

    if send_telegram and (not bot_token or not chat_id):
        raise RuntimeError(
            "Telegram is enabled but missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID. "
            "Set env vars or use --telegram-bot-token and --telegram-chat-id."
        )

    try:
        if args.daily:
            run_daily(
                args.time,
                output_dir=args.output_dir,
                headless=is_headless,
                max_coins=args.max_coins,
                send_telegram=send_telegram,
                cleanup_images=cleanup_images,
                bot_token=bot_token,
                chat_id=chat_id,
            )
        elif args.once:
            run_cycle(
                output_dir=args.output_dir,
                headless=is_headless,
                max_coins=args.max_coins,
                send_telegram=send_telegram,
                cleanup_images=cleanup_images,
                bot_token=bot_token,
                chat_id=chat_id,
            )
        else:
            run_interval(
                interval_minutes=args.interval_minutes,
                output_dir=args.output_dir,
                headless=is_headless,
                max_coins=args.max_coins,
                send_telegram=send_telegram,
                cleanup_images=cleanup_images,
                bot_token=bot_token,
                chat_id=chat_id,
            )
    except Exception as exc:
        print(f"Error: {exc}")