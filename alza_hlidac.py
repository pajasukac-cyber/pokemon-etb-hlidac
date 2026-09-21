import re
import time
import requests

MAX_PRICE = 2500

DISCORD_TOKEN = __import__("os").environ.get("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = __import__("os").environ.get("DISCORD_CHANNEL_ID", "")

# Alza discovery pages. We do not connect to Alza directly from GitHub Actions,
# because Alza's Cloudflare blocks GitHub IPs.
ALZA_DISCOVERY_URLS = [
    "https://www.alza.cz/the-pokemon-company/v3460.htm",
    "https://www.alza.cz/hracky/pokemon-booster-boxy-a-specialni-boxy/18903046.htm",
    "https://www.alza.cz/search.htm?exps=elite%20trainer%20box",
]

STATE_FILE = "state_alza.json"


def jina_get(url):
    try:
        r = requests.get(
            "https://r.jina.ai/" + url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/plain",
            },
        )
        if r.status_code == 200 and len(r.text) > 200:
            return r.text
        print("Jina HTTP:", r.status_code, url)
    except Exception as e:
        print("Jina chyba:", e)
    return ""


def parse_price(text):
    prices = []

    # Examples: 1 899 Kč, 1 899,- Kč, 1899 Kč
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

    return prices[0] if prices else None


def alza_find():
    result = []
    seen = set()

    for source in ALZA_DISCOVERY_URLS:
        print("Hledám přes:", source)
        content = jina_get(source)
        if not content:
            continue

        candidates = []

        # Markdown links returned by Jina.
        for m in re.finditer(
            r"\[([^\]]*Elite Trainer Box[^\]]*)\]\((https?://www\.alza\.cz/[^)\s]+)\)",
            content,
            re.I,
        ):
            candidates.append((m.group(1).strip(), m.group(2)))

        # Raw Alza links.
        for href in re.findall(
            r"https?://www\.alza\.cz/[^)\s\"<>]+",
            content,
            flags=re.I,
        ):
            candidates.append(("", href))

        for name, href in candidates:
            href = href.replace("&amp;", "&").rstrip("/")
            combined = (name + " " + href).lower()

            if href in seen:
                continue
            if "elite-trainer-box" not in combined and "elite trainer box" not in combined:
                continue
            if "alza.cz" not in href.lower():
                continue
            if "/search" in href.lower():
                continue

            seen.add(href)
            result.append((name or href.rsplit("/", 1)[-1], href))

    return result


def alza_check(name, url):
    content = jina_get(url)
    if not content:
        return None, False

    low = content.lower()
    price = parse_price(content)

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

    available = False
    if not unavailable:
        available = any(x in low for x in [
            "do košíku",
            "do kosiku",
            "koupit",
            "skladem",
        ])

    return price, available


def discord_alert(name, price, url):
    if not DISCORD_TOKEN or not DISCORD_CHANNEL_ID:
        print("Discord secrets nejsou nastavené.")
        return

    api = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"

    payload = {
        "content": (
            "🚨 **ALZA ETB ALERT**\n"
            f"**{name}**\n"
            f"💰 **{price:,} Kč**\n"
            f"🔗 {url}"
        ).replace(",", " ")
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
    import json
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    import json
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    print("=" * 55)
    print("       ALZA ETB HLÍDAČ - SAMOSTATNĚ")
    print("=" * 55)
    print(f"Limit: {MAX_PRICE} Kč")
    print("=" * 55)

    state = load_state()
    products = alza_find()

    print()
    print("===== ALZA.CZ =====")
    print("Nalezeno ETB:", len(products))

    if not products:
        print("⚠️ Alza přes dostupné zdroje nevrátila žádné ETB.")

    changed = False

    for name, url in products:
        print()
        print("Kontroluji:", name)

        price, available = alza_check(name, url)

        print("Cena:", price)
        print("Dostupnost:", available)

        qualifies = price is not None and price <= MAX_PRICE and available
        key = url

        if qualifies:
            print("🚨 PODMÍNKY SPLNĚNY!")

            if not state.get(key, False):
                discord_alert(name, price, url)
                print("✅ Upozornění odesláno.")
            else:
                print("ℹ️ Upozornění už bylo odesláno, neopakuji.")
        else:
            print("Podmínky nesplněny.")

        if state.get(key, False) != qualifies:
            state[key] = qualifies
            changed = True

    if changed:
        save_state(state)
        print()
        print("💾 Stav uložen.")
    else:
        print()
        print("ℹ️ Stav se nezměnil.")


if __name__ == "__main__":
    main()
