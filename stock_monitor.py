"""
e2zstore stock monitor - watches several products, alerts via ntfy.

Uses the WooCommerce Store API (JSON) instead of scraping product pages,
because the pages are behind a Cloudflare challenge and return 403 to
scripts. Querying by product ID also works while an item is out of stock
(the store hides out-of-stock items from slug/search queries).

To find a new product's ID, search the API including out-of-stock items:
    https://e2zstore.com/wp-json/wc/store/v1/products?search=<words>&stock_status=outofstock

Modes:
    python stock_monitor.py            # loop forever (run on your own PC)
    python stock_monitor.py --single   # one round of checks, remembers state
                                       # in state.json (used by GitHub Actions)
    python stock_monitor.py --test     # one round, prints results, no alerts
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
# Products to watch. "id" is the WooCommerce product ID.
PRODUCTS = [
    {
        "id": 8673,
        "name": "Rs. 1000 PSN Wallet Top Up",
        "url": "https://e2zstore.com/product/rs-1000-playstation-network-wallet-top-up/",
    },
    {
        "id": 8590,
        "name": "Rs. 2000 PSN Wallet Top Up",
        "url": "https://e2zstore.com/product/rs-2000-playstation-network-wallet-top-up/",
    },
    {
        "id": 8626,
        "name": "Rs. 3000 PSN Wallet Top Up",
        "url": "https://e2zstore.com/product/rs-3000-playstation-network-wallet-top-up/",
    },
    {
        "id": 8677,
        "name": "Rs. 4000 PSN Wallet Top Up",
        "url": "https://e2zstore.com/product/rs-4000-playstation-network-wallet-top-up/",
    },
    {
        "id": 383915,
        "name": "Rs. 5000 PSN Wallet Top Up",
        "url": "https://e2zstore.com/product/rs-5000-playstation-store-gift-card-wallet-top-up/",
    },
    {
        "id": 529864,
        "name": "Rs. 7000 PSN Wallet Top Up",
        "url": "https://e2zstore.com/product/rs-7000-sony-playstation-store-gift-card-wallet-top-up/",
    },
    {
        "id": 509537,
        "name": "Rs. 8000 PSN Wallet Top Up",
        "url": "https://e2zstore.com/product/rs-8000-sony-playstation-store-gift-card-wallet-top-up/",
    },
    {
        "id": 529871,
        "name": "Rs. 9000 PSN Wallet Top Up",
        "url": "https://e2zstore.com/product/rs-9000-sony-playstation-store-gift-card-wallet-top-up/",
    },
]

API_BASE = "https://e2zstore.com/wp-json/wc/store/v1/products"

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")    # or paste your topic here for local runs

CHECK_INTERVAL_SECONDS = 120              # loop mode only
JITTER_SECONDS = (5, 30)                  # loop mode only
MAX_CONSECUTIVE_ERRORS = 5                # alert if the monitor seems broken
STATE_FILE = Path(__file__).with_name("state.json")
# ==========================================

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


def check_stock(product_id: int) -> tuple[bool | None, str]:
    """
    Returns (status, detail):
        (True, ...)  -> in stock and purchasable
        (False, ...) -> out of stock
        (None, ...)  -> could not determine (network error, blocked, API changed)
    """
    try:
        response = session.get(f"{API_BASE}/{product_id}", timeout=20)
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


def new_product_state() -> dict:
    return {"notified": False, "errors": 0, "error_alerted": False}


def process(product: dict, state: dict) -> dict:
    """Runs one check for one product and sends any alerts. Returns new state."""
    name = product["name"]
    status, detail = check_stock(product["id"])

    if status is None:
        state["errors"] += 1
        log(f"[{name}] Check failed ({state['errors']} in a row): {detail}")
        if state["errors"] >= MAX_CONSECUTIVE_ERRORS and not state["error_alerted"]:
            if notify(f"Stock monitor problem: {name}",
                      f"{state['errors']} failed checks in a row.\nLast error: {detail}",
                      tags="warning"):
                state["error_alerted"] = True
        return state

    if state["error_alerted"]:
        notify(f"Stock monitor recovered: {name}", "Checks are working normally again.",
               priority="low", tags="white_check_mark")
    state["errors"] = 0
    state["error_alerted"] = False

    if status:
        if not state["notified"]:
            log(f"[{name}] IN STOCK! ({detail})")
            if notify(f"IN STOCK: {name}",
                      f"{detail}\nTap to open the product page.",
                      priority="urgent", tags="rotating_light", click=product["url"]):
                state["notified"] = True
        else:
            log(f"[{name}] Still in stock ({detail}), alert already sent.")
    else:
        if state["notified"]:
            log(f"[{name}] Went back out of stock. Resetting alert.")
            state["notified"] = False
        else:
            log(f"[{name}] Out of stock ({detail}).")
    return state


def run_round(states: dict) -> dict:
    """Checks every product once. `states` maps product ID (str) -> state."""
    for i, product in enumerate(PRODUCTS):
        key = str(product["id"])
        states[key] = process(product, states.get(key) or new_product_state())
        if i < len(PRODUCTS) - 1:
            time.sleep(random.uniform(1, 3))   # small gap between requests
    return states


def load_states() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    if "notified" in data:                    # old single-product format
        return {str(PRODUCTS[0]["id"]): {**new_product_state(), **data}}
    return data


def main() -> None:
    args = set(sys.argv[1:])
    log(f"Watching {len(PRODUCTS)} product(s): " + ", ".join(p["name"] for p in PRODUCTS))

    if "--ping" in args:
        ok = notify("Stock monitor test", "If you see this, ntfy alerts work.", tags="bell")
        sys.exit(0 if ok else 1)

    if "--test" in args:
        ok = True
        for product in PRODUCTS:
            status, detail = check_stock(product["id"])
            log(f"[{product['name']}] Result: {status} ({detail})")
            ok = ok and status is not None
        sys.exit(0 if ok else 1)

    if "--single" in args:
        states = run_round(load_states())
        STATE_FILE.write_text(json.dumps(states))
        return

    states: dict = {}
    while True:
        states = run_round(states)
        sleep_time = CHECK_INTERVAL_SECONDS + random.randint(*JITTER_SECONDS)
        log(f"Next check in {sleep_time}s\n")
        time.sleep(sleep_time)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")
