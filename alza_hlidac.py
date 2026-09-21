import os
import re
import json
import requests

MAX_PRICE = 2500

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")

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
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain"},
        )
        if r.status_code == 200 and len(r.text) > 200:
            return r.text
        print("Jina HTTP:", r.status_code, url)
    except Exception as e:
        print("Jina chyba:", e)
    return ""


def parse_price(text):
    for m in re.finditer(
        r"(?<!\d)(\d{1,2}(?:[\s\u00a0]\d{3})|\d{3,5})\s*(?:,-\s*)?(?:Kč|CZK)",
        text,
        re.I,
    ):
        raw = m.group(1).replace(" ", "").replace("\u00a0", "")
        try:
            p = int(raw)
            if 300 <= p <= 100000:
                return p
        except Exception:
            pass
    return None


def alza_find():
    result = []
    seen = set()

    for source in ALZA_DISCOVERY_URLS:
        print()
        print("Hledám přes:", source)
        content = jina_get(source)

        if not content:
            print("⚠️ Žádná data z Jina.")
            continue

        print("Jina vrátila znaků:", len(content))

        # Nejdřív diagnostika: ukaž řádky, kde se vůbec vyskytuje
        # "Elite Trainer". Tím zjistíme skutečný formát odpovědi Alzy.
        matches = []
        for line in content.splitlines():
            if "elite trainer" in line.lower() or "elite-trainer" in line.lower():
                matches.append(line.strip())

        print("Řádky s Elite Trainer:", len(matches))
        for line in matches[:12]:
            print("DEBUG ETB:", line[:500])

        # Hledej všechny alza.cz odkazy a až potom filtruj.
        # Neomezujeme se na /hracky/, protože Alza může mít URL jinak.
        all_urls = re.findall(
            r'https?://(?:www\.)?alza\.cz/[^)\s"<>]+',
            content,
            flags=re.I,
        )

        # Jina Markdown odkazy: vezmeme i název produktu.
        markdown_links = re.findall(
            r'\[([^\]]+)\]\((https?://(?:www\.)?alza\.cz/[^)\s]+)\)',
            content,
            flags=re.I,
        )

        candidates = [(name.strip(), href) for name, href in markdown_links]
        candidates += [("", href) for href in all_urls]

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

    print()
    print("Celkem kandidátů ETB:", len(result))

    return result

def alza_check(name, url):
    content = jina_get(url)
    if not content:
        return None, False

    low = content.lower()
    price = parse_price(content)

    unavailable = any(x in low for x in [
        "momentálně nedostupné", "momentalne nedostupne",
        "není skladem", "neni skladem",
        "vyprodáno", "vyprodano",
        "hlídat dostupnost", "hlidat dostupnost",
    ])

    available = False
    if not unavailable:
        available = any(x in low for x in [
            "do košíku", "do kosiku", "koupit", "skladem",
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

    changed = False

    for name, url in products:
        print()
        print("Kontroluji:", name)

        price, available = alza_check(name, url)
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

    # Vždy vytvoř stavový soubor, i když zatím nebylo nic nalezeno.
    # Díky tomu workflow nespadne na "state_alza.json did not match any files".
    save_state(state)

    if changed:
        print()
        print("💾 Stav uložen.")
    else:
        print()
        print("ℹ️ Stav se nezměnil.")


if __name__ == "__main__":
    main()
