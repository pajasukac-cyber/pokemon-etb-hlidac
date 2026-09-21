import os
import re
import json
import time
import requests
from playwright.sync_api import sync_playwright

MAX_PRICE = 2500
STATE_FILE = "state_alza.json"

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")

SEARCHES = [
    "elite trainer box",
    "pokemon elite trainer box",
]


def discord_alert(name, price, url):
    if not DISCORD_TOKEN or not DISCORD_CHANNEL_ID:
        print("Discord secrets nejsou nastavené.")
        return

    api = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    payload = {
        "content": (
            "🚨 **ALZA ETB ALERT**\n"
            f"**{name}**\n"
            f"💰 **{price} Kč**\n"
            f"🔗 {url}"
        )
    }

    try:
        r = requests.post(
            api,
            headers={
                "Authorization": f"Bot {DISCORD_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        print("Discord:", r.status_code)
    except Exception as e:
        print("Discord chyba:", e)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def parse_jsonld_products(page):
    products = []

    try:
        scripts = page.locator('script[type="application/ld+json"]').all_text_contents()
    except Exception:
        scripts = []

    for raw in scripts:
        try:
            data = json.loads(raw)
        except Exception:
            continue

        stack = data if isinstance(data, list) else [data]

        for item in stack:
            if not isinstance(item, dict):
                continue

            if item.get("@type") == "Product":
                products.append(item)

            graph = item.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if isinstance(node, dict) and node.get("@type") == "Product":
                        products.append(node)

    return products


def product_from_jsonld(product):
    name = str(product.get("name") or "").strip()
    url = str(product.get("url") or "").strip()

    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}

    price = offers.get("price")
    availability = str(offers.get("availability") or "").lower()

    try:
        price = int(float(str(price).replace(",", "."))) if price is not None else None
    except Exception:
        price = None

    available = any(x in availability for x in [
        "instock",
        "limitedavailability",
    ])

    return name, url, price, available


def find_etbs(page):
    result = []
    seen = set()

    for query in SEARCHES:
        url = "https://www.alza.cz/search.htm?exps=" + query.replace(" ", "%20")
        print()
        print("Alza vyhledávání:", query)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
        except Exception as e:
            print("Navigace chyba:", e)
            continue

        print("URL:", page.url)
        print("TITLE:", page.title())

        body = page.locator("body").inner_text(timeout=10000)
        if "bezpečnost" in body.lower() or "cloudflare" in body.lower():
            print("⚠️ Alza stále vrací bezpečnostní blokaci.")
            print(body[:500].replace("\n", " "))
            continue

        cards = page.locator(".browsingitem")
        count = cards.count()
        print("Nalezeno produktových karet:", count)

        for i in range(count):
            try:
                card = cards.nth(i)
                text = card.inner_text(timeout=3000)
                low = text.lower()

                if "elite trainer box" not in low and "elite-trainer-box" not in low:
                    continue

                href = ""
                links = card.locator("a")
                for j in range(links.count()):
                    h = links.nth(j).get_attribute("href")
                    if h and "alza.cz" in h and ".htm" in h:
                        href = h
                        break

                if not href:
                    continue

                if href.startswith("/"):
                    href = "https://www.alza.cz" + href

                name = ""
                try:
                    name = card.locator("h2, h3").first.inner_text(timeout=1000).strip()
                except Exception:
                    name = text.split("\n")[0].strip()

                key = href.rstrip("/")
                if key not in seen:
                    seen.add(key)
                    result.append((name, href))
                    print("ETB:", name, href)

            except Exception:
                pass

    return result


def check_product(page, name, url):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
    except Exception as e:
        print("Produkt navigace chyba:", e)
        return None, False

    # First choice: structured Product JSON-LD.
    for product in parse_jsonld_products(page):
        p_name, p_url, price, available = product_from_jsonld(product)

        if "elite trainer box" in p_name.lower() and price is not None:
            return price, available

    # Fallback: visible page text.
    try:
        text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        text = ""

    low = text.lower()

    prices = []
    for m in re.finditer(
        r"(?<!\d)(\d{1,2}(?:[\s\u00a0]\d{3})|\d{3,5})\s*(?:,-\s*)?(?:Kč|CZK)",
        text,
        re.I,
    ):
        raw = m.group(1).replace(" ", "").replace("\u00a0", "")
        try:
            p = int(raw)
            if 300 <= p <= 100000:
                prices.append(p)
        except Exception:
            pass

    price = prices[0] if prices else None

    unavailable = any(x in low for x in [
        "momentálně nedostupné",
        "momentalne nedostupne",
        "není skladem",
        "neni skladem",
        "vyprodáno",
        "vyprodano",
        "hlídat dostupnost",
        "hlidat dostupnost",
    ])

    available = not unavailable and any(x in low for x in [
        "do košíku",
        "do kosiku",
        "koupit",
        "skladem",
    ])

    return price, available


def main():
    print("=" * 55)
    print("       ALZA ETB HLÍDAČ - PLAYWRIGHT")
    print("=" * 55)
    print(f"Limit: {MAX_PRICE} Kč")
    print("=" * 55)

    state = load_state()
    products = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="cs-CZ",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        try:
            products = find_etbs(page)

            # Check every discovered product directly.
            print()
            print("===== ALZA.CZ =====")
            print("Nalezeno ETB:", len(products))

            changed = False

            for name, url in products:
                print()
                print("Kontroluji:", name)

                price, available = check_product(page, name, url)

                print("Cena:", price)
                print("Dostupnost:", available)

                qualifies = price is not None and price <= MAX_PRICE and available

                if qualifies:
                    print("🚨 PODMÍNKY SPLNĚNY!")

                    if not state.get(url, False):
                        discord_alert(name, price, url)
                        print("✅ Upozornění odesláno.")
                    else:
                        print("ℹ️ Upozornění už bylo odesláno, neopakuji.")
                else:
                    print("Podmínky nesplněny.")

                if state.get(url, False) != qualifies:
                    state[url] = qualifies
                    changed = True

            save_state(state)

            if changed:
                print()
                print("💾 Stav uložen.")
            else:
                print()
                print("ℹ️ Stav se nezměnil.")

        finally:
            browser.close()


if __name__ == "__main__":
    main()
