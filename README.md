# e2zstore stock monitor

Checks every 5 minutes (via GitHub Actions) whether these e2zstore.com products are
in stock, and sends a push notification through [ntfy](https://ntfy.sh):

- Rs. 1000 PSN Wallet Top Up (ID 8673)
- Rs. 2000 PSN Wallet Top Up (ID 8590)

It reads the store's WooCommerce Store API (`/wp-json/wc/store/v1/products/<id>`)
because the product pages are behind a Cloudflare challenge.

## Adding a product
1. Find its ID (works even while it's out of stock):
   `https://e2zstore.com/wp-json/wc/store/v1/products?search=<words>&stock_status=outofstock`
2. Add an entry to `PRODUCTS` at the top of `stock_monitor.py` and commit.

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
