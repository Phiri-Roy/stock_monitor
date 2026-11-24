"""
e2zstore stock monitor - Rs. 1000 PSN Wallet Top Up, alerts via ntfy.

Uses the WooCommerce Store API (JSON) instead of scraping the product page,
because the product page is behind a Cloudflare challenge and returns 403
to scripts. The API endpoint by product ID also works while the item is
out of stock (the store hides out-of-stock items from slug/search queries).

Modes:
    python stock_monitor.py            # loop forever (run on your own PC)
    python stock_monitor.py --single   # one check, remembers state in
                                       # state.json (used by GitHub Actions)
    python stock_monitor.py --test     # one check, prints result, no alerts
    python stock_monitor.py --ping     # send a test notification to ntfy

Settings come from environment variables (GitHub secrets in the cloud):
    NTFY_TOPIC   your secret ntfy topic name (required for alerts)
    NTFY_SERVER  optional, defaults to https://ntfy.sh
"""

import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ==========================================
# CONFIGURATION
# ==========================================
PRODUCT_ID = 8673
PRODUCT_NAME = "Rs. 1000 PSN Wallet Top Up"
PRODUCT_URL = "https://e2zstore.com/product/rs-1000-playstation-network-wallet-top-up/"

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")    # or paste your topic here for local runs

CHECK_INTERVAL_SECONDS = 120              # loop mode only
JITTER_SECONDS = (5, 30)                  # loop mode only
MAX_CONSECUTIVE_ERRORS = 5                # alert if the monitor seems broken
STATE_FILE = Path(__file__).with_name("state.json")
# ==========================================

API_URL = f"https://e2zstore.com/wp-json/wc/store/v1/products/{PRODUCT_ID}"

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
})


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def notify(title: str, message: str, priority: str = "default",
           tags: str = "", click: str = "") -> bool:
    """Sends a push notification through ntfy."""
    if not NTFY_TOPIC:
        log("NTFY_TOPIC is not set; cannot send notification.")
        return False
    headers = {"Title": title.encode("utf-8"), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
    try:
        r = requests.post(f"{NTFY_SERVER}/{NTFY_TOPIC}",
                          data=message.encode("utf-8"), headers=headers, timeout=20)
        r.raise_for_status()
        log(f"Notification sent: {title}")
        return True
    except requests.RequestException as e:
        log(f"Failed to send notification: {e}")
        return False


def check_stock() -> tuple[bool | None, str]:
    """
    Returns (status, detail):
        (True, ...)  -> in stock and purchasable
        (False, ...) -> out of stock
        (None, ...)  -> could not determine (network error, blocked, API changed)
    """
    try:
        response = session.get(API_URL, timeout=20)
    except requests.RequestException as e:
        return None, f"network error: {e}"

    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"

    try:
        data = response.json()
    except ValueError:
        return None, "response was not JSON (possibly blocked by Cloudflare)"

    if "is_in_stock" not in data:
        return None, "unexpected API response (no 'is_in_stock' field)"

    in_stock = bool(data["is_in_stock"]) and bool(data.get("is_purchasable", True))
    text = (data.get("stock_availability") or {}).get("text") or ("In stock" if in_stock else "Out of stock")
    prices = data.get("prices") or {}
    price = prices.get("price")
    if price and str(price).isdigit():
        minor = int(prices.get("currency_minor_unit", 2))
        text += f", price Rs. {int(price) / 10**minor:g}"
    return in_stock, text


def process(state: dict) -> dict:
    """Runs one check and sends any alerts. Updates and returns state."""
    status, detail = check_stock()

    if status is None:
        state["errors"] += 1
        log(f"Check failed ({state['errors']} in a row): {detail}")
        if state["errors"] >= MAX_CONSECUTIVE_ERRORS and not state["error_alerted"]:
            if notify("Stock monitor problem",
                      f"{state['errors']} failed checks in a row.\nLast error: {detail}",
                      priority="default", tags="warning"):
                state["error_alerted"] = True
        return state

    if state["error_alerted"]:
        notify("Stock monitor recovered", "Checks are working normally again.",
               priority="low", tags="white_check_mark")
    state["errors"] = 0
    state["error_alerted"] = False

    if status:
        if not state["notified"]:
            log(f"IN STOCK! ({detail})")
            if notify(f"IN STOCK: {PRODUCT_NAME}",
                      f"{detail}\nTap to open the product page.",
                      priority="urgent", tags="rotating_light", click=PRODUCT_URL):
                state["notified"] = True
        else:
            log(f"Still in stock ({detail}), alert already sent.")
    else:
        if state["notified"]:
            log("Went back out of stock. Resetting alert.")
            state["notified"] = False
        else:
            log(f"Out of stock ({detail}).")
    return state


def load_state() -> dict:
    state = {"notified": False, "errors": 0, "error_alerted": False}
    try:
        state.update(json.loads(STATE_FILE.read_text()))
    except (FileNotFoundError, ValueError):
        pass
    return state


def main() -> None:
    args = set(sys.argv[1:])
    log(f"Watching: {API_URL}")

    if "--ping" in args:
        ok = notify("Stock monitor test", "If you see this, ntfy alerts work.",
                    tags="bell", click=PRODUCT_URL)
        sys.exit(0 if ok else 1)

    if "--test" in args:
        status, detail = check_stock()
        log(f"Result: {status} ({detail})")
        sys.exit(0 if status is not None else 1)

    if "--single" in args:
        state = process(load_state())
        STATE_FILE.write_text(json.dumps(state))
        return

    state = {"notified": False, "errors": 0, "error_alerted": False}
    while True:
        state = process(state)
        sleep_time = CHECK_INTERVAL_SECONDS + random.randint(*JITTER_SECONDS)
        log(f"Next check in {sleep_time}s\n")
        time.sleep(sleep_time)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")
