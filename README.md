# e2zstore stock monitor

Checks every 5 minutes (via GitHub Actions) whether the Rs. 1000 PSN Wallet Top Up
on e2zstore.com is in stock, and sends a push notification through [ntfy](https://ntfy.sh).

It reads the store's WooCommerce Store API (`/wp-json/wc/store/v1/products/8673`)
because the product page itself is behind a Cloudflare challenge.

## Setup
1. Repo settings → Secrets and variables → Actions → New repository secret:
   `NTFY_TOPIC` = your ntfy topic name.
2. Actions tab → enable workflows → "Stock monitor" → Run workflow with **ping** ticked
   to get a test notification.

## Local use
```
pip install -r requirements.txt
set NTFY_TOPIC=your-topic
python stock_monitor.py --test     # one check, no alerts
python stock_monitor.py            # loop on your PC
```
