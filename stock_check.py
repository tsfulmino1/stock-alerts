"""
Multi-retailer stock checker -> ntfy.sh push notification

Reads the product list from products.json (managed by the Drop Watch phone
app) and checks each one against its retailer. Sends a push notification
the moment a tracked product goes in stock.

WHAT THIS DOES NOT DO
It does not add items to a cart or complete checkout automatically. That
would be a purchasing bot, which violates every one of these retailers'
Terms of Service. This only watches and alerts -- you still do the buying.

RELIABILITY BY RETAILER (be aware of this):
- Pokemon Center: most reliable. It's a Shopify store, and Shopify exposes
  a public product JSON endpoint we can read directly and honestly.
- Best Buy: fairly reliable via embedded schema.org markup, but Best Buy
  runs bot detection (Akamai) that can block or CAPTCHA automated requests,
  especially if checked too frequently.
- Target: least reliable. Target's bot protection is aggressive and their
  page structure changes often. Treat Target results as a bonus signal,
  not something to depend on -- keep the Target app's own alerts on too.
"""

import requests
import json
import os
import re

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "changeme-your-unique-topic-name")
PRODUCTS_FILE = "products.json"
STATE_FILE = "stock_state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def check_bestbuy(url):
    """Returns True/False/None. Relies on schema.org availability markup."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        print(f"  request failed: {e}")
        return None
    if resp.status_code != 200:
        print(f"  got status {resp.status_code} (may be blocked)")
        return None
    html = resp.text
    if '"availability":"http://schema.org/InStock"' in html:
        return True
    if '"availability":"http://schema.org/OutOfStock"' in html:
        return False
    if "Sold Out" in html:
        return False
    if "Add to Cart" in html:
        return True
    return None


def check_target(url):
    """Returns True/False/None. Target's markup shifts often -- best effort."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        print(f"  request failed: {e}")
        return None
    if resp.status_code != 200:
        print(f"  got status {resp.status_code} (Target blocks bots often)")
        return None
    html = resp.text
    if '"availability_status":"IN_STOCK"' in html:
        return True
    if '"availability_status":"OUT_OF_STOCK"' in html:
        return False
    if re.search(r'"is_out_of_stock"\s*:\s*true', html):
        return False
    if re.search(r'"is_out_of_stock"\s*:\s*false', html):
        return True
    if "Out of stock" in html:
        return False
    if "Add to cart" in html:
        return True
    return None


def check_pokemoncenter(url):
    """Returns True/False/None. Uses Shopify's public product JSON endpoint."""
    json_url = url.split("?")[0].rstrip("/")
    if not json_url.endswith(".json"):
        json_url += ".json"
    try:
        resp = requests.get(json_url, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        print(f"  request failed: {e}")
        return None
    if resp.status_code != 200:
        print(f"  got status {resp.status_code}")
        return None
    try:
        data = resp.json()
        variants = data.get("product", {}).get("variants", [])
        if not variants:
            return None
        return any(v.get("available") for v in variants)
    except (ValueError, KeyError) as e:
        print(f"  couldn't parse response: {e}")
        return None


CHECKERS = {
    "bestbuy": check_bestbuy,
    "target": check_target,
    "pokemoncenter": check_pokemoncenter,
}


def send_notification(name, url):
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=f"{name} looks IN STOCK. Tap to open the page.".encode("utf-8"),
        headers={
            "Title": "Drop alert",
            "Priority": "urgent",
            "Tags": "rotating_light",
            "Click": url,
        },
    )
    print(f"  notification sent -> {url}")


def main():
    products = load_json(PRODUCTS_FILE, [])
    state = load_json(STATE_FILE, {})

    if not products:
        print("No products in products.json yet -- nothing to check.")
        return

    for product in products:
        pid = product["id"]
        name = product["name"]
        retailer = product["retailer"]
        url = product["url"]

        checker = CHECKERS.get(retailer)
        print(f"[{retailer}] {name}")
        if not checker:
            print(f"  unknown retailer '{retailer}', skipping")
            continue

        in_stock = checker(url)
        was_in_stock = state.get(pid, False)
        print(f"  in_stock={in_stock}")

        if in_stock is True and not was_in_stock:
            send_notification(name, url)
            state[pid] = True
        elif in_stock is False:
            state[pid] = False
        # if in_stock is None, keep last known state -- don't false-alarm

    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
