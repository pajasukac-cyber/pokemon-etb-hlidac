import time
import re
import os
import json
import requests
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options


MAX_PRICE = 2500
CHECK_EVERY = 300
STATE_FILE = "state.json"

STORES = {
    "SMARTY.CZ": "https://www.smarty.cz/elite-trainer-box-4c14603",
    "POKEMON4U.CZ": "https://www.pokemon4u.cz/pokemon-elite-trainer-box/",
    "ALZA.CZ": "https://www.alza.cz/hracky/pokemon-booster-boxy-a-specialni-boxy/18903046.htm",
    "GOOD-LUCK.CZ": "https://www.good-luck.cz/etb",
    "POKEMALL.CZ": "https://www.pokemall.cz/kategorie/elite-trainer-box/",
    "TCGSHOP.CZ": "https://www.tcgshop.cz/elite-trainer-boxy/",
    "LUXOR.CZ": "https://www.luxor.cz/c/11589/pokemon",
    "KNIHY-DOBROVSKY.CZ": "https://www.knihydobrovsky.cz/elite-trainer-box",
}


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "").strip()


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def discord_alert(shop, product, price, url):
    msg = (
        "🚨 **POKÉMON ETB HLÍDAČ**\n\n"
        f"**Obchod:** {shop}\n"
        f"**Produkt:** {product}\n"
        f"**Cena:** {price:,} Kč\n"
        "**SKLADEM**\n"
        f"{url}"
    ).replace(",", " ")

    r = requests.post(
        f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
        headers={
            "Authorization": f"Bot {TOKEN}",
            "Content-Type": "application/json",
        },
        json={"content": msg},
        timeout=20,
    )
    print("Discord:", r.status_code)
    if r.ok:
        print("✅ Upozornění odesláno.")
    else:
        print("❌ Discord chyba:", r.text[:300])


def make_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=cs-CZ")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })
    except Exception:
        pass
    return driver


def driver_alive(driver):
    try:
        driver.current_url
        return True
    except Exception:
        return False


def safe_text(driver):
    try:
        return driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return ""


def price_from_element(driver, selectors):
    for selector in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                txt = el.text.strip()
                # Prefer prices with Kč / ,- and ignore tiny numbers.
                m = re.search(r"(\d[\d\s\xa0]*)\s*(?:Kč|,-)", txt, re.I)
                if m:
                    n = int(re.sub(r"\s+", "", m.group(1)))
                    if 300 <= n <= 100000:
                        return n
        except Exception:
            pass
    return None



def price_from_html(driver):
    try:
        html = driver.page_source.replace("\xa0", " ")
        vals = []
        for m in re.findall(r"(\d[\d\s]{2,8})\s*(?:Kč|,-)", html, re.I):
            try:
                n = int(re.sub(r"\s+", "", m))
                if 300 <= n <= 100000:
                    vals.append(n)
            except Exception:
                pass
        return min(vals) if vals else None
    except Exception:
        return None


def price_from_text(text):
    vals = []
    for m in re.findall(r"(\d[\d\s\xa0]*)\s*(?:Kč|,-)", text, re.I):
        try:
            n = int(re.sub(r"\s+", "", m))
            if 300 <= n <= 100000:
                vals.append(n)
        except Exception:
            pass
    return min(vals) if vals else None


def normalize_url(href):
    if not href:
        return ""
    return href.split("#")[0].rstrip("/")


def valid_product_link(href, name=""):
    if not href:
        return False
    s = (href + " " + name).lower()

    if "elite trainer box" not in s and "elite-trainer-box" not in s:
        return False

    # Navigation/category/login/filter links are not products.
    bad = [
        "elite-trainer-box-4c14603",
        "/elite-trainer-boxy/",
        "/elite-trainer-boxy#",
        "/login/",
        "javascript:",
        "mailto:",
    ]
    if any(x in href.lower() for x in bad):
        return False

    return True


def collect_links(driver):
    result = []
    seen = set()

    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = normalize_url(a.get_attribute("href"))
            name = a.text.strip()
            if not valid_product_link(href, name):
                continue

            # Require a product-looking link rather than generic navigation.
            key = href.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append((name, href))
        except Exception:
            pass

    return result


def explicit_stock(text):
    t = text.lower()

    negatives = [
        "není skladem",
        "neni skladem",
        "momentálně nedostupné",
        "momentalne nedostupne",
        "produkt není skladem",
        "produkt neni skladem",
        "vyprodáno",
        "vyprodano",
        "předobjednávka",
        "predobjednavka",
        "předobjednat",
        "predobjednat",
        "připravujeme",
        "pripravujeme",
    ]
    if any(x in t for x in negatives):
        return False

    positives = [
        "skladem celkem",
        "skladem na prodejně",
        "skladem na prodejne",
        "skladem >",
        "skladem",
        "do košíku",
        "do kosiku",
        "koupit",
    ]
    return any(x in t for x in positives)


# ---------- SMARTY ----------
def smarty_find(driver):
    driver.get(STORES["SMARTY.CZ"])
    time.sleep(3)

    result = []
    seen = set()

    # 1) Běžné odkazy v DOM.
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = normalize_url(a.get_attribute("href"))
            name = a.text.strip()
            low = href.lower()
            if not href or href in seen:
                continue
            if "smarty.cz/" not in low or "elite-trainer-box" not in low:
                continue
            if "4c14603" in low:
                continue
            seen.add(href)
            result.append((name, href))
        except Exception:
            pass

    # 2) Záloha: produktové URL přímo z HTML. Funguje i když jsou
    # produktové karty v headless Chromu bez textového <a> elementu.
    try:
        html = driver.page_source.replace("\\/", "/")
        patterns = [
            r"https?://www\\.smarty\\.cz/[^\"'<>\\s]+elite-trainer-box[^\"'<>\\s]*",
            r"https?://smarty\\.cz/[^\"'<>\\s]+elite-trainer-box[^\"'<>\\s]*",
        ]
        for pattern in patterns:
            for href in re.findall(pattern, html, flags=re.I):
                href = normalize_url(href)
                low = href.lower()
                if not href or href in seen:
                    continue
                if "elite-trainer-box" not in low or "4c14603" in low:
                    continue
                seen.add(href)
                result.append(("", href))
    except Exception:
        pass

    # 3) Produktové URL ověříme přes H1; pokud H1 v headless režimu
    # selže, ponecháme kandidáta — detail se následně kontroluje v smarty_check.
    verified = []
    for old_name, href in result:
        try:
            driver.get(href)
            time.sleep(1.0)
            h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
            if "elite trainer box" in h1.lower():
                verified.append((h1, href))
            elif old_name and "elite trainer box" in old_name.lower():
                verified.append((old_name, href))
        except Exception:
            if old_name and "elite trainer box" in old_name.lower():
                verified.append((old_name, href))

    # Bez duplicit.
    unique = []
    seen_urls = set()
    for name, href in verified:
        if href not in seen_urls:
            seen_urls.add(href)
            unique.append((name or "Pokémon Elite Trainer Box", href))
    return unique


def smarty_check(driver, name, url):
    driver.get(url)
    time.sleep(1.2)
    text = safe_text(driver)

    price = price_from_element(driver, [
        ".price-final",
        "[class*='price-final']",
        "[class*='product-price']",
    ])
    if price is None:
        price = price_from_text(text)

    t = text.lower()
    good = [
        "skladem celkem",
        "skladem na prodejně",
        "skladem na prodejne",
    ]
    bad = [
        "připravujeme",
        "pripravujeme",
        "předobjednávka",
        "predobjednavka",
        "předobjednat",
        "predobjednat",
        "expedice bude upřesněna",
        "expedice bude upresnena",
    ]
    available = any(x in t for x in good) and not any(x in t for x in bad)
    return price, available


# ---------- POKEMON4U ----------
def pokemon4u_find(driver):
    driver.get(STORES["POKEMON4U.CZ"])
    time.sleep(2)

    candidates = []
    seen = set()

    # Najdeme jen odkazy, které vypadají jako skutečné produktové URL.
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = normalize_url(a.get_attribute("href"))
            if not href or href in seen:
                continue

            low = href.lower()

            if "pokemon4u.cz/" not in low:
                continue
            if "elite-trainer-box" not in low:
                continue
            if low.rstrip("/") == STORES["POKEMON4U.CZ"].rstrip("/").lower():
                continue
            if "/strana-" in low or "/login/" in low:
                continue

            # Kategorie není produkt.
            slug = low.rstrip("/").split("/")[-1]
            if slug in ("pokemon-elite-trainer-box", "elite-trainer-box"):
                continue

            seen.add(href)
            candidates.append(href)
        except Exception:
            pass

    # Každý kandidát jednou otevřeme a ověříme podle H1.
    # Název bereme přímo z produktové stránky, takže už se nemůže objevit
    # "Přejít na obsah" ani prázdný název.
    result = []
    for href in candidates:
        try:
            driver.get(href)
            time.sleep(0.8)

            h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
            if "elite trainer box" not in h1.lower():
                continue

            result.append((h1, href))
        except Exception:
            pass

    # Zrušíme případné duplicity.
    unique = []
    seen_urls = set()
    for name, href in result:
        if href not in seen_urls:
            seen_urls.add(href)
            unique.append((name, href))

    return unique


def pokemon4u_check(driver, name, url):
    driver.get(url)
    time.sleep(1.2)
    text = safe_text(driver)

    price = price_from_element(driver, [
        ".price-final",
        ".product-price",
        "[class*='price-final']",
    ])
    if price is None:
        price = price_from_text(text)

    t = text.lower()
    available = (
        any(x in t for x in ["skladem", "do košíku", "do kosiku", "koupit"])
        and not any(x in t for x in [
            "není skladem", "neni skladem", "vyprodáno", "vyprodano",
            "předobjednávka", "predobjednavka"
        ])
    )
    return price, available


# ---------- ALZA ----------
def alza_find(driver):
    driver.get(STORES["ALZA.CZ"])
    time.sleep(4)

    candidates = []
    seen = set()

    # Zkusíme i odscrollovat stránku, protože Alza může část produktů
    # doplnit až po scrollování.
    try:
        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.8)
    except Exception:
        pass

    # 1) Běžné odkazy v DOM.
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = normalize_url(a.get_attribute("href"))
            name = a.text.strip()
            if not href or href in seen:
                continue

            low = href.lower()
            if "/hracky/pokemon-tcg-" not in low:
                continue
            if "elite-trainer-box" not in low:
                continue
            if "18903046.htm" in low:
                continue

            seen.add(href)
            candidates.append((name, href))
        except Exception:
            pass

    # 2) Záloha z HTML.
    try:
        html = driver.page_source.replace("\\/", "/")
        pattern = r"https?://www\\.alza\\.cz/hracky/pokemon-tcg-[^\"'<>\\s]+elite-trainer-box[^\"'<>\\s]*"
        for href in re.findall(pattern, html, flags=re.I):
            href = normalize_url(href)
            low = href.lower()
            if not href or href in seen:
                continue
            if "18903046.htm" in low or "elite-trainer-box" not in low:
                continue
            seen.add(href)
            candidates.append(("", href))
    except Exception:
        pass

    # Ověření H1, ale při problému s H1 nepouštíme kandidáta automaticky,
    # pokud URL už jednoznačně obsahuje elite-trainer-box.
    result = []
    for original_name, href in candidates:
        try:
            driver.get(href)
            time.sleep(1.0)
            try:
                h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
            except Exception:
                h1 = original_name.strip()

            if "elite trainer box" in h1.lower():
                result.append((h1, href))
            elif "elite-trainer-box" in href.lower():
                result.append((original_name or "Pokémon Elite Trainer Box", href))
        except Exception:
            if "elite-trainer-box" in href.lower():
                result.append((original_name or "Pokémon Elite Trainer Box", href))

    unique = []
    seen_urls = set()
    for name, href in result:
        if href not in seen_urls:
            seen_urls.add(href)
            unique.append((name, href))
    return unique


def alza_check(driver, name, url):
    driver.get(url)
    time.sleep(1.5)
    text = safe_text(driver)

    price = price_from_element(driver, [
        ".price-box__price",
        ".price-box__price-text",
        "[class*='price-box'] [class*='price']",
    ])

    if price is None:
        price = price_from_text("\n".join(text.splitlines()[:120]))

    if price is None:
        return None, False

    t = text.lower()

    negative = [
        "momentálně nedostupné",
        "momentalne nedostupne",
        "není skladem",
        "neni skladem",
        "vyprodáno",
        "vyprodano",
        "předobjednávka",
        "predobjednavka",
    ]

    if any(x in t for x in negative):
        return price, False

    positive = [
        "skladem >",
        "skladem",
        "do košíku",
        "do kosiku",
        "koupit",
    ]

    return price, any(x in t for x in positive)

# ---------- GOOD-LUCK ----------
def goodluck_find(driver):
    driver.get(STORES["GOOD-LUCK.CZ"])
    time.sleep(2)

    # Good-Luck's ETB category itself contains the authoritative product cards:
    # each card shows the product name, its own stock status, and its own price.
    # We capture those card links and later inspect the same product page.
    result = []
    seen = set()

    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = normalize_url(a.get_attribute("href"))
            name = a.text.strip()
            low = (href + " " + name).lower()

            if not href or href in seen:
                continue
            if "elite-trainer-box" not in low and "elite trainer box" not in low:
                continue
            if href.lower().rstrip("/") == STORES["GOOD-LUCK.CZ"].rstrip("/").lower():
                continue
            if href.lower().rstrip("/").endswith("/etb"):
                continue
            if "/nastaveni-cookies" in href.lower():
                continue

            # Ignore generic links whose text isn't a product title.
            if name.lower() in ("", "rychlé info", "rychle info"):
                continue

            seen.add(href)
            result.append((name, href))
        except Exception:
            pass

    # Verify with product H1 and use the actual title.
    verified = []
    for old_name, href in result:
        try:
            driver.get(href)
            time.sleep(0.8)
            h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
            if "elite trainer box" in h1.lower():
                verified.append((h1, href))
        except Exception:
            pass

    return verified


def goodluck_check(driver, name, url):
    driver.get(url)
    time.sleep(1.3)

    text = safe_text(driver)
    t = text.lower()

    # IMPORTANT:
    # Good-Luck product pages can contain unrelated "Skladem" text in
    # recommendations/other blocks. We therefore look first for the product
    # title and inspect the nearest product-detail container for its own
    # availability and price.
    product_container = None

    try:
        h1 = driver.find_element(By.TAG_NAME, "h1")
        product_container = h1
        # Walk up the DOM until the element contains an ETB title + price/stock.
        for _ in range(6):
            parent = product_container.find_element(By.XPATH, "./..")
            pt = parent.text.strip()
            if ("elite trainer box" in pt.lower()
                    and ("skladem" in pt.lower()
                         or "není skladem" in pt.lower()
                         or "neni skladem" in pt.lower())
                    and ("Kč" in pt or ",-" in pt)):
                product_container = parent
                break
            product_container = parent
    except Exception:
        product_container = None

    scope_text = product_container.text if product_container is not None else text
    scope_low = scope_text.lower()

    # The exact product is unavailable if its own scope says so.
    if "není skladem" in scope_low or "neni skladem" in scope_low:
        return price_from_text(scope_text), False

    # Otherwise require a stock signal in the same product scope.
    available = bool(re.search(r"\bskladem\b", scope_low))

    price = price_from_text(scope_text)
    if price is None:
        price = price_from_html(driver) if "price_from_html" in globals() else None

    return price, available

# ---------- POKEMALL ----------
def pokemall_find(driver):
    pages = [
        "https://www.pokemall.cz/kategorie/elite-trainer-box/",
        "https://www.pokemall.cz/kategorie/elite-trainer-box/strana-2/",
    ]

    candidates = []
    seen = set()

    for page in pages:
        driver.get(page)
        time.sleep(1.5)

        for a in driver.find_elements(By.CSS_SELECTOR, 'a[href*="elite-trainer-box"]'):
            try:
                href = normalize_url(a.get_attribute("href"))
                name = a.text.strip()

                if not href or href in seen:
                    continue

                low = href.lower()

                # Všechny skutečné produktové URL mají v cestě
                # "elite-trainer-box". Ignorujeme pouze kategorii, login
                # a podobné navigační odkazy. Konkrétní produkt potom
                # ověříme otevřením stránky a kontrolou H1.
                if "/kategorie/" in low or "/login/" in low:
                    continue
                if "elite-trainer-box" not in low:
                    continue
                if low.startswith("javascript:") or low.startswith("mailto:"):
                    continue

                seen.add(href)
                candidates.append((name, href))
            except Exception:
                pass

    # Ověříme každou URL na produktové stránce a vezmeme H1.
    verified = []
    seen_urls = set()

    for old_name, href in candidates:
        if href in seen_urls:
            continue
        seen_urls.add(href)

        try:
            driver.get(href)
            time.sleep(0.7)
            h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()

            if "elite trainer box" not in h1.lower():
                continue

            verified.append((h1, href))
        except Exception:
            pass

    return verified


def pokemall_check(driver, name, url):
    driver.get(url)
    time.sleep(1.2)
    text = safe_text(driver)
    t = text.lower()

    # Cena: preferuj cenu produktu z meta/price elementu.
    price = price_from_element(driver, [
        ".price",
        "[class*='price']",
        "meta[itemprop='price']",
    ])

    if price is None:
        price = price_from_text("\n".join(text.splitlines()[:120]))

    if price is None and "price_from_html" in globals():
        price = price_from_html(driver)

    if price is None:
        return None, False

    # Pokemall je Shoptet. Kategorie používá filtr "Na skladě".
    # Na detailu je proto bezpečné akceptovat "Skladem" pouze bez explicitní
    # informace o nedostupnosti.
    negative = [
        "vyprodáno",
        "vyprodano",
        "není skladem",
        "neni skladem",
        "není dostupný",
        "neni dostupny",
        "tato varianta není dostupná",
        "tato varianta neni dostupna",
        "zvolená varianta není k dispozici",
        "zvolena varianta neni k dispozici",
    ]
    if any(x in t for x in negative):
        return price, False

    # Silný signál: přímo "Skladem" / "Na skladě".
    strong_stock = [
        "skladem",
        "na skladě",
        "na sklade",
    ]
    return price, any(x in t for x in strong_stock)
# ---------- TCGSHOP ----------
def tcgshop_find(driver):
    driver.get(STORES["TCGSHOP.CZ"])
    time.sleep(2)

    result = []
    seen = set()

    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = normalize_url(a.get_attribute("href"))
            name = a.text.strip()
            low = (href + " " + name).lower()

            if not href or href in seen:
                continue
            if "/login/" in low:
                continue
            if "/pokemon-" not in low:
                continue
            if "elite-trainer-box" not in low and "elite trainer box" not in low:
                continue
            if href.lower().rstrip("/") == STORES["TCGSHOP.CZ"].rstrip("/").lower():
                continue

            seen.add(href)
            result.append((name, href))
        except Exception:
            pass

    verified = []
    for old_name, href in result:
        try:
            driver.get(href)
            time.sleep(0.8)
            h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
            if "elite trainer box" in h1.lower():
                verified.append((h1, href))
        except Exception:
            pass

    return verified


def tcgshop_check(driver, name, url):
    driver.get(url)
    time.sleep(1.4)
    text = safe_text(driver)

    try:
        h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
    except Exception:
        h1 = name

    if "elite trainer box" not in h1.lower():
        return None, False

    price = price_from_element(driver, [
        ".woocommerce-Price-amount",
        "p.price",
        "span.woocommerce-Price-amount",
    ])

    if price is None:
        price = price_from_text("\n".join(text.splitlines()[:160]))
    if price is None:
        price = price_from_html(driver)

    if price is None:
        return None, False

    t = text.lower()
    if any(x in t for x in [
        "není skladem", "neni skladem",
        "vyprodáno", "vyprodano",
        "předobjednávka", "predobjednavka"
    ]):
        return price, False

    # TCGshop's current product page exposes "Skladem (1 ks)" and "Do košíku".
    return price, any(x in t for x in [
        "skladem",
        "do košíku",
        "do kosiku",
        "přidat do košíku",
        "pridat do kosiku",
    ])


# ---------- LUXOR ----------
# Luxor kontrolujeme přímo přes produktové stránky.
# Kategorie není spolehlivá pro vyprodané produkty – produkt může existovat,
# ale v kategorickém výpisu se nezobrazí.
LUXOR_ETB_URLS = [
    "https://www.luxor.cz/v/2175941/pokemon-tcg-mega-evolution-03-perfect-order-elite-trainer-box",
    "https://www.luxor.cz/v/2125031/pokemon-tcg-scarlet-violet-105-white-flare-elite-trainer-box",
    "https://www.luxor.cz/v/2125032/pokemon-tcg-scarlet-violet-105-black-bolt-elite-trainer-box",
    "https://www.luxor.cz/v/2205423/pokemon-tcg-scarlet-violet-10-destined-rivals-elite-trainer-box",
    "https://www.luxor.cz/v/2090204/pokemon-tcg-scarlet-violet-85-prismatic-evolutions-elite-trainer-box",
    "https://www.luxor.cz/v/2022144/pokemon-tcg-sv45-paldean-fates-elite-trainer-box",
    "https://www.luxor.cz/v/1983273/pokemon-tcg-scarlet-violet-151-elite-trainer-box",
    "https://www.luxor.cz/v/1992053/pokemon-tcg-scarlet-violet-04-paradox-rift-elite-trainer-box",
]

def luxor_find(driver):
    # Žádné hledání v kategorii. Vracíme pouze přímé produktové URL.
    result = []
    for url in LUXOR_ETB_URLS:
        name = url.rsplit("/", 1)[-1].replace("-", " ").title()
        result.append((name, url))
    return result

def _find_luxor_json_price(obj):
    """Najde Product -> offers -> price v JSON-LD, bez hledání náhodných čísel."""
    if isinstance(obj, dict):
        offers = obj.get("offers")
        if isinstance(offers, dict):
            price = offers.get("price")
            if price not in (None, ""):
                try:
                    return int(round(float(str(price).replace(",", "."))))
                except Exception:
                    pass
        elif isinstance(offers, list):
            for offer in offers:
                found = _find_luxor_json_price({"offers": offer})
                if found is not None:
                    return found

        for value in obj.values():
            found = _find_luxor_json_price(value)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = _find_luxor_json_price(value)
            if found is not None:
                return found

    return None

def _product_area_text(driver):
    """Vrátí text nejmenšího rozumného DOM kontejneru obsahujícího H1 produktu."""
    try:
        return driver.execute_script("""
        const h = document.querySelector('h1');
        if (!h) return document.body.innerText || '';
        let e = h;
        for (let i = 0; i < 7 && e; i++, e = e.parentElement) {
            const t = (e.innerText || '').trim();
            if (t.length >= 80 &&
                (t.includes('Kč') || /Do košíku|Do kosiku|Koupit|Skladem|Není skladem|Nedostupné/i.test(t))) {
                return t;
            }
        }
        return h.parentElement ? h.parentElement.innerText : h.innerText;
        """) or safe_text(driver)
    except Exception:
        return safe_text(driver)


def _price_from_product_jsonld(driver):
    try:
        scripts = driver.find_elements(By.CSS_SELECTOR, "script[type='application/ld+json']")
        for script in scripts:
            raw = script.get_attribute("textContent") or ""
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            p = _find_luxor_json_price(data)
            if p is not None:
                return p
    except Exception:
        pass
    return None


def _price_from_product_meta(driver):
    selectors = [
        "meta[itemprop='price']",
        "meta[property='product:price:amount']",
    ]
    for selector in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                raw = (el.get_attribute("content") or "").strip()
                if re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
                    p = int(round(float(raw.replace(",", "."))))
                    if 100 <= p <= 100000:
                        return p
        except Exception:
            pass
    return None


def _price_from_product_area(text):
    # Jen celé částky s Kč v hlavním produktu; žádné "339" z bannerů.
    vals = []
    for m in re.finditer(r'(?<!\d)(\d{1,2}(?:[ .]\d{3})|\d{3,5})\s*Kč\b', text, re.I):
        p = int(re.sub(r'\D', '', m.group(1)))
        if 100 <= p <= 100000:
            vals.append(p)
    if not vals:
        return None
    # U ETB je hlavní cena typicky 1 000–20 000 Kč.
    return vals[0]



def _main_product_text(driver):
    """Vrátí co nejmenší viditelný blok kolem H1 produktu."""
    try:
        txt = driver.execute_script("""
        const h = document.querySelector('h1');
        if (!h) return document.body.innerText || '';
        let e = h;
        for (let i = 0; i < 10 && e; i++, e = e.parentElement) {
            const t = (e.innerText || '').replace(/\\u00a0/g, ' ').trim();
            if (t.length >= 20 && t.length <= 1800 &&
                /(Kč|Do košíku|Do kosiku|Koupit|Skladem|Není skladem|Neni skladem|Nedostupné|Vyprodáno|Produkt je vyprodaný|Prodej ukončen)/i.test(t)) {
                return t;
            }
        }
        return h.parentElement ? h.parentElement.innerText : h.innerText;
        """) or ""
        return txt.strip()
    except Exception:
        return safe_text(driver)


def _product_jsonld_info(driver):
    """Vrátí (price, availability) z Product/Offer JSON-LD."""
    def walk(obj):
        if isinstance(obj, dict):
            typ = str(obj.get("@type", "")).lower()
            offers = obj.get("offers")

            if "product" in typ or offers is not None:
                if isinstance(offers, list):
                    for offer in offers:
                        p, a = walk({"offers": offer})
                        if p is not None or a is not None:
                            return p, a
                elif isinstance(offers, dict):
                    price = offers.get("price")
                    currency = offers.get("priceCurrency")
                    availability = str(offers.get("availability", "")).lower()

                    p = None
                    if price not in (None, ""):
                        try:
                            p = int(round(float(str(price).replace(",", "."))))
                        except Exception:
                            pass

                    a = None
                    if "instock" in availability or "limitedavailability" in availability:
                        a = True
                    elif any(x in availability for x in (
                        "outofstock", "soldout", "discontinued", "unavailable"
                    )):
                        a = False

                    if p is not None or a is not None:
                        return p, a

            for value in obj.values():
                p, a = walk(value)
                if p is not None or a is not None:
                    return p, a

        elif isinstance(obj, list):
            for value in obj:
                p, a = walk(value)
                if p is not None or a is not None:
                    return p, a

        return None, None

    try:
        for script in driver.find_elements(By.CSS_SELECTOR, "script[type='application/ld+json']"):
            raw = script.get_attribute("textContent") or ""
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            p, a = walk(data)
            if p is not None or a is not None:
                return p, a
    except Exception:
        pass

    return None, None


def luxor_check(driver, name, url):
    driver.get(url)
    time.sleep(1.5)

    main = _main_product_text(driver)
    low = main.lower()

    # Nejprve skutečný stav hlavního produktu.
    negative = [
        "není skladem", "neni skladem",
        "není k dispozici", "neni k dispozici",
        "vyprodáno", "vyprodano",
        "prodej ukončen", "prodej ukoncen",
        "nedostupné", "nedostupne",
    ]
    if any(x in low for x in negative):
        available = False
    else:
        # Pouze explicitní akce/stav u hlavního produktu.
        available = bool(re.search(
            r'\bdo\s+košíku\b|\bdo\s+kosiku\b|\bkoupit\b|\brezervovat\b|\bskladem\b',
            low, re.I
        ))

    # Cena pouze ze strukturovaných dat nebo z hlavního produktového bloku.
    price, json_available = _product_jsonld_info(driver)
    if price is None:
        price = _price_from_product_meta(driver)

    if price is None and available:
        price = _price_from_product_area(main)

    # JSON-LD smí potvrdit sklad jen tehdy, pokud hlavní viditelný blok
    # neobsahuje žádný negativní stav.
    if not any(x in low for x in negative) and json_available is True:
        available = True
    elif json_available is False:
        available = False

    return price, available


def knihy_dobrovsky_find(driver):
    driver.get(STORES["KNIHY-DOBROVSKY.CZ"])
    time.sleep(2)

    # Kategorie má 43 ETB a stránkování po 24 kusech. Načteme další produkty,
    # pokud je tlačítko dostupné.
    for _ in range(3):
        try:
            buttons = driver.find_elements(By.XPATH, "//*[contains(normalize-space(.), 'Zobrazit dalších 24 produktů')]")
            target = None
            for b in buttons:
                try:
                    if b.is_displayed() and b.is_enabled():
                        target = b
                        break
                except Exception:
                    pass
            if target is None:
                break
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
            time.sleep(0.3)
            driver.execute_script("arguments[0].click();", target)
            time.sleep(1.2)
        except Exception:
            break

    result = []
    seen = set()
    for a in driver.find_elements(By.TAG_NAME, "a"):
        try:
            href = normalize_url(a.get_attribute("href"))
            name = a.text.strip()
            low = (href + " " + name).lower()
            if not href or href in seen:
                continue
            if "knihydobrovsky.cz/" not in low:
                continue
            if "elite-trainer-box" not in low and "elite trainer box" not in low:
                continue
            if "/hra/" not in low and "/hracka/" not in low:
                continue
            seen.add(href)
            result.append((name, href))
        except Exception:
            pass

    verified = []
    for old_name, href in result:
        try:
            driver.get(href)
            time.sleep(0.6)
            h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
            if "elite trainer box" in h1.lower():
                verified.append((h1, href))
        except Exception:
            pass

    return verified




def knihy_dobrovsky_check(driver, name, url):
    driver.get(url)
    time.sleep(1.2)

    main = _main_product_text(driver)
    low = main.lower()

    # Když je produkt vyprodaný/nedostupný, okamžitě False a žádná náhodná cena.
    negative = [
        "nedostupné", "nedostupne",
        "produkt je vyprodaný", "produkt je vyprodany",
        "vyprodáno", "vyprodano",
        "není skladem", "neni skladem",
        "momentálně nedostupné", "momentalne nedostupne",
        "předobjednávka", "predobjednavka",
    ]
    if any(x in low for x in negative):
        return None, False

    price, json_available = _product_jsonld_info(driver)

    if price is None:
        price = _price_from_product_meta(driver)

    # Dostupnost jen z hlavního produktu.
    available = bool(re.search(
        r'\bdo\s+košíku\b|\bdo\s+kosiku\b|\bkoupit\b|\bskladem\b',
        low, re.I
    ))

    if json_available is True:
        available = True
    elif json_available is False:
        available = False

    # Textová cena jen pokud je produkt skutečně dostupný.
    if price is None and available:
        price = _price_from_product_area(main)

    return price, available


def check_store(driver, shop, state):
    print(f"\n===== {shop} =====")

    try:
        links = FINDERS[shop](driver)
    except Exception as e:
        print("CHYBA PŘI HLEDÁNÍ:", e)
        return False

    print("Nalezeno ETB:", len(links))
    if not links:
        try:
            print("DEBUG URL:", driver.current_url)
            print("DEBUG TITLE:", driver.title)
            body_preview = safe_text(driver).replace("\\n", " ")[:300]
            print("DEBUG BODY:", body_preview)
        except Exception:
            pass
    changed = False

    for name, url in links:
        try:
            print(f"\nKontroluji: {name}")
            price, available = CHECKERS[shop](driver, name, url)
            print("Cena:", price)
            print("Dostupnost:", available)

            key = f"{shop}|{url}"
            qualifies = price is not None and price <= MAX_PRICE and available
            previous = state.get(key, False)

            if qualifies:
                print("🚨 PODMÍNKY SPLNĚNY!")
                if not previous:
                    discord_alert(shop, name, price, url)
                else:
                    print("ℹ️ Upozornění už bylo odesláno, neopakuji.")
            else:
                print("Podmínky nesplněny.")

            if previous != qualifies:
                state[key] = qualifies
                changed = True

        except Exception as e:
            print("CHYBA PRODUKTU:", e)

    return changed


def main():
    print("======================================")
    print("       POKÉMON ETB HLÍDAČ - CLOUD")
    print("======================================")
    print(f"{len(STORES)} obchodů | limit: {MAX_PRICE} Kč | plánovaná kontrola: 5 min")

    if not TOKEN or not CHANNEL_ID:
        raise RuntimeError("Chybí DISCORD_TOKEN nebo DISCORD_CHANNEL_ID v prostředí.")

    state = load_state()
    driver = make_driver()
    changed = False

    try:
        print("\n======================================")
        print(datetime.now().strftime("%H:%M:%S"), "- NOVÁ KONTROLA")
        print("======================================")

        for shop in STORES:
            changed = check_store(driver, shop, state) or changed

        if changed:
            save_state(state)
            print("✅ Stav uložen do state.json")
        else:
            print("ℹ️ Stav se nezměnil.")

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
