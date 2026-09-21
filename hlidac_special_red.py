# SAMOSTATNÝ SPECIÁLNÍ HLÍDAČ – hlavní hlídač se tímto souborem nemění.
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


SPECIAL_MAX_PRICE = 3500
CHECK_EVERY = 300
STATE_FILE = "state_special.json"

STORES = {
    "SMARTY.CZ": "https://www.smarty.cz/elite-trainer-box-4c14603",
    "POKEMON4U.CZ": "https://www.pokemon4u.cz/pokemon-elite-trainer-box/",
    "GOOD-LUCK.CZ": "https://www.good-luck.cz/etb",
    "POKEMALL.CZ": "https://www.pokemall.cz/kategorie/elite-trainer-box/",
    "TCGSHOP.CZ": "https://www.tcgshop.cz/elite-trainer-boxy/",
    "LUXOR.CZ": "https://www.luxor.cz/c/11589/pokemon",
    "KNIHY-DOBROVSKY.CZ": "https://www.knihydobrovsky.cz/elite-trainer-box",
}


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "").strip()

# Speciální ETB, která hlídáme samostatně. Všechny mají vlastní limit 3 500 Kč.
# Ostatní ETB se tímto hlídačem vůbec nezabývají.
SPECIAL_ETBS = {
    "MEGA EVOLUTION - ELITE TRAINER BOX": 3500,
    "MEGA LUCARIO - ELITE TRAINER BOX": 3500,
    "PRISMATIC EVOLUTIONS - ELITE TRAINER BOX": 3500,
    "ASCENDED HEROES - ELITE TRAINER BOX": 3500,
}


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


def discord_alert(shop, product, price, url, reason="první splnění", old_price=None):
    if old_price is not None:
        price_line = f"**{price:,} Kč**  (předtím {old_price:,} Kč)"
        reason_line = "📉 Cena právě klesla."
    else:
        price_line = f"**{price:,} Kč**"
        reason_line = "🟢 Cena je pod speciálním limitem."

    payload = {
        "content": "🔴 **🔥 SPECIÁLNÍ ETB ALERT 🔥**",
        "embeds": [{
            "title": f"🚨 {product}",
            "description": (
                f"**Obchod:** {shop}\n"
                f"**Cena:** {price_line}\n"
                f"**Limit:** 3 500 Kč\n"
                f"{reason_line}\n\n"
                f"[Otevřít produkt]({url})"
            ),
            "color": 16711680
        }]
    }

    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {TOKEN}"},
            json=payload,
            timeout=20
        )
        print(f"📨 Discord: HTTP {r.status_code}")
        if r.status_code >= 300:
            print(r.text[:500])
    except Exception as e:
        print(f"❌ Discord chyba: {e}")

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
    """
    Smarty:
    1) projde několik stran ETB kategorie,
    2) navíc provede cílené interní hledání všech 4 speciálních ETB.
    Tím se produkt neztratí jen proto, že momentálně není na první stránce.
    """
    category = STORES["SMARTY.CZ"]
    candidates = []
    seen = set()

    def collect_from_current_page():
        for a in driver.find_elements(By.TAG_NAME, "a"):
            try:
                href = normalize_url(a.get_attribute("href"))
                name = a.text.strip()
                if not href or href in seen:
                    continue

                low = (href + " " + name).lower()
                if "smarty.cz/" not in low:
                    continue
                if "elite-trainer-box" not in low and "elite trainer box" not in low:
                    continue

                # Kategorie / navigace, ne konkrétní produkt.
                if low.rstrip("/") == category.rstrip("/").lower():
                    continue
                if "/vyhledavani" in low:
                    continue

                seen.add(href)
                candidates.append((name, href))
            except Exception:
                pass

    # --- 1) ETB kategorie, více stran ---
    for page_num in range(1, 8):
        try:
            page_url = category if page_num == 1 else f"{category}?pg={page_num}"
            driver.get(page_url)
            time.sleep(1.2)
            collect_from_current_page()
        except Exception:
            pass

    # --- 2) Cílené interní vyhledávání ---
    queries = [
        "Mega Evolution 01 Elite Trainer Box",
        "Mega Lucario Elite Trainer Box",
        "Prismatic Evolutions Elite Trainer Box",
        "Ascended Heroes Elite Trainer Box",
    ]

    for query in queries:
        try:
            search_url = "https://www.smarty.cz/Vyhledavani?query=" + quote_plus(query)
            driver.get(search_url)
            time.sleep(1.5)
            collect_from_current_page()
        except Exception:
            pass

    # --- 3) Přímé známé produktové URL ---
    # Tyto produkty jsou na Smarty ověřené. Přidáváme je natvrdo, aby je
    # hlídač sledoval i tehdy, když se nezobrazí v kategorii/vyhledávání.
    direct_products = [
        (
            "Pokémon TCG: ME01 - Mega Evolution Elite Trainer Box",
            "https://www.smarty.cz/Pokemon-TCG-ME01-Mega-Evolution-Elite-Trainer-Box-4p244516",
        ),
        (
            "Pokémon TCG: SV8.5 Prismatic Evolutions - Elite Trainer Box",
            "https://www.smarty.cz/Pokemon-TCG-SV8-5-Prismatic-Evolutions-Elite-Trainer-Box-4p207320",
        ),
    ]

    for known_name, href in direct_products:
        href = normalize_url(href)
        if href not in seen:
            seen.add(href)
            candidates.append((known_name, href))

    # --- 4) Ověření skutečné produktové stránky ---
    verified = []
    seen_urls = set()
    known_direct = {normalize_url(u): n for n, u in direct_products}

    for old_name, href in candidates:
        if href in seen_urls:
            continue
        seen_urls.add(href)

        # U ověřených přímých URL nemusíme spoléhat na text odkazu/H1.
        if href in known_direct:
            verified.append((known_direct[href], href))
            continue

        try:
            driver.get(href)
            time.sleep(0.7)
            h1 = driver.find_element(By.TAG_NAME, "h1").text.strip()
            # Vynecháme generické navigační odkazy typu jen "Elite Trainer Box".
            if (
                "elite trainer box" in h1.lower()
                and h1.lower().strip() != "elite trainer box"
            ):
                verified.append((h1, href))
        except Exception:
            pass

    return verified

def smarty_check(driver, name, url):
    driver.get(url)
    time.sleep(1.5)

    # Na Smarty se některé produktové údaje v headless Chrome nepromítnou
    # spolehlivě do běžného textu. Proto čteme současně DOM i zdroj stránky.
    try:
        body_text = driver.execute_script("return document.body.innerText || '';")
    except Exception:
        body_text = safe_text(driver)

    try:
        html = driver.page_source or ""
    except Exception:
        html = ""

    def parse_price(value_text):
        vals = []
        for raw in re.findall(r"(\d[\d\s\xa0]*)\s*Kč", value_text or "", re.I):
            try:
                n = int(re.sub(r"\s+", "", raw))
                if 300 <= n <= 100000:
                    vals.append(n)
            except Exception:
                pass
        return min(vals) if vals else None

    price = None

    # 1) JSON-LD Product / Offer
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

            stack = data if isinstance(data, list) else [data]
            while stack:
                item = stack.pop()
                if not isinstance(item, dict):
                    continue

                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])

                if "offers" in item:
                    offers = item["offers"]
                    offers_list = offers if isinstance(offers, list) else [offers]
                    for offer in offers_list:
                        if isinstance(offer, dict):
                            raw_price = offer.get("price")
                            if raw_price is not None:
                                try:
                                    n = int(round(float(str(raw_price).replace(",", "."))))
                                    if 300 <= n <= 100000:
                                        price = n
                                        break
                                except Exception:
                                    pass
                if price is not None:
                    break
            if price is not None:
                break
    except Exception:
        pass

    # 2) Meta itemprop=price
    if price is None:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, "meta[itemprop='price']"):
                raw = (el.get_attribute("content") or "").strip()
                if re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
                    n = int(round(float(raw.replace(",", "."))))
                    if 300 <= n <= 100000:
                        price = n
                        break
        except Exception:
            pass

    # 3) HTML / DOM jako fallback.
    # Smarty na produktové stránce uvádí zákaznickou cenu před "bez DPH".
    if price is None:
        for source in (body_text, html):
            m = re.search(
                r"(\d[\d\s\xa0]*)\s*Kč\s*(?!bez\s*DPH)",
                source or "",
                re.I
            )
            if m:
                try:
                    n = int(re.sub(r"\s+", "", m.group(1)))
                    if 300 <= n <= 100000:
                        price = n
                        break
                except Exception:
                    pass

    if price is None:
        price = parse_price(body_text)
    if price is None:
        price = parse_price(html)

    # Dostupnost posuzujeme pouze podle textů Smarty.
    # "Dostupné na prodejně" znamená skutečný sklad na prodejně.
    t = (body_text or "").lower()
    h = (html or "").lower()

    bad = [
        "připravujeme",
        "pripravujeme",
        "předobjednávka",
        "predobjednavka",
        "předobjednat",
        "predobjednat",
        "expedice bude upřesněna",
        "expedice bude upresnena",
        "neznámá dostupnost",
        "neznamá dostupnost",
        "neznama dostupnost",
    ]

    good = [
        "skladem celkem",
        "skladem eshop",
        "skladem na prodejně",
        "skladem na prodejne",
        "dostupné na prodejně",
        "dostupne na prodejne",
    ]

    available = any(x in t for x in good)

    # Některé texty mohou být schované v HTML atributu/skriptu.
    if not available:
        available = any(x in h for x in [
            "skladem celkem",
            "skladem eshop",
            "skladem na prodejně",
            "skladem na prodejne",
            "dostupné na prodejně",
            "dostupne na prodejne",
        ])

    if any(x in t for x in bad):
        # "Dostupné na prodejně" má přednost před obecným textem
        # o neznámé online dostupnosti.
        if not any(x in t for x in [
            "dostupné na prodejně",
            "dostupne na prodejne",
            "skladem na prodejně",
            "skladem na prodejne",
            "skladem eshop",
            "skladem celkem",
        ]):
            available = False

    return price, available

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

def _find_luxor_json_offer(obj):
    """Vrátí (price, availability) z Product/Offer JSON-LD."""
    if isinstance(obj, dict):
        offers = obj.get("offers")

        if isinstance(offers, dict):
            price = offers.get("price")
            availability = str(offers.get("availability") or "").lower()
            parsed_price = None

            if price not in (None, ""):
                try:
                    parsed_price = int(round(float(str(price).replace(",", "."))))
                except Exception:
                    pass

            if parsed_price is not None or availability:
                return parsed_price, availability

        elif isinstance(offers, list):
            for offer in offers:
                found = _find_luxor_json_offer({"offers": offer})
                if found != (None, ""):
                    return found

        for value in obj.values():
            found = _find_luxor_json_offer(value)
            if found != (None, ""):
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = _find_luxor_json_offer(value)
            if found != (None, ""):
                return found

    return None, ""


def luxor_check(driver, name, url):
    driver.get(url)
    time.sleep(1.5)

    full_text = safe_text(driver)
    price = None
    json_availability = ""

    # 1) JSON-LD Product/Offer – pokud Luxor dostupnost poskytuje,
    # je to nejpřesnější údaj.
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

            p, availability = _find_luxor_json_offer(data)

            if p is not None and price is None:
                price = p

            if availability:
                json_availability = availability
                break
    except Exception:
        pass

    if json_availability:
        if "outofstock" in json_availability or "soldout" in json_availability:
            return price, False
        if "instock" in json_availability:
            return price, True
        if "preorder" in json_availability:
            return price, False

    # 2) Meta Product price.
    if price is None:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, "meta[itemprop='price']"):
                raw = (el.get_attribute("content") or "").strip()
                if re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
                    price = int(round(float(raw.replace(",", "."))))
                    break
        except Exception:
            pass

    # 3) Viditelná cena.
    if price is None:
        try:
            candidates = driver.execute_script("""
                return Array.from(document.querySelectorAll('*'))
                  .map(e => (e.innerText || '').trim())
                  .filter(t => /^\\d{1,2}(?:[ .]\\d{3})\\s*Kč$/.test(t)
                            || /^\\d{3,5}\\s*Kč$/.test(t));
            """)

            parsed = []
            for raw in candidates or []:
                value = int(re.sub(r"[^0-9]", "", raw))
                if 100 <= value <= 99999:
                    parsed.append(value)

            if parsed:
                from collections import Counter
                price = Counter(parsed).most_common(1)[0][0]
        except Exception:
            pass

    # DŮLEŽITÉ:
    # Nepoužíváme celý body text stránky.
    # Luxor má v hlavičce obecné "co nemáme skladem, objednáme u dodavatele".
    # Starý parser proto chybně označil produkt jako skladem.
    #
    # Dostupnost hledáme pouze v konkrétním bloku produktu.
    scope_text = ""

    try:
        h1 = driver.find_element(By.TAG_NAME, "h1")
        node = h1

        for _ in range(8):
            try:
                parent = node.find_element(By.XPATH, "./..")
            except Exception:
                break

            pt = (parent.text or "").strip()

            if (
                name.lower() in pt.lower()
                and re.search(r"\d[\d\s\xa0]*\s*Kč", pt, re.I)
            ):
                scope_text = pt

                # Pokud blok obsahuje skutečný nákupní ovladač,
                # dál už ho nerozšiřujeme.
                try:
                    controls = parent.find_elements(
                        By.XPATH,
                        ".//*[self::button or self::a]"
                        "[contains(translate(normalize-space(.), "
                        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ', "
                        "'abcdefghijklmnopqrstuvwxyzáčďéěíňóřšťúůýž'), "
                        "'do košíku') "
                        "or contains(translate(normalize-space(.), "
                        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ', "
                        "'abcdefghijklmnopqrstuvwxyzáčďéěíňóřšťúůýž'), "
                        "'koupit') "
                        "or contains(translate(normalize-space(.), "
                        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ', "
                        "'abcdefghijklmnopqrstuvwxyzáčďéěíňóřšťúůýž'), "
                        "'rezervovat')]"
                    )

                    if controls:
                        break
                except Exception:
                    pass

            node = parent

        if not scope_text:
            scope_text = h1.text or ""

    except Exception:
        scope_text = ""

    scope_low = scope_text.lower()

    # Explicitní nedostupnost konkrétního produktu.
    bad = [
        "není skladem", "neni skladem",
        "není k dispozici", "neni k dispozici",
        "vyprodáno", "vyprodano",
        "předobjednávka", "predobjednavka",
        "předobjednat", "predobjednat",
        "cena v předprodeji", "cena v predprodeji",
    ]

    if any(x in scope_low for x in bad):
        return price, False

    # Samotné slovo "skladem" už NESTAČÍ.
    # Uznáváme pouze konkrétní nákupní/dostupnostní signály.
    good = [
        "do košíku", "do kosiku",
        "přidat do košíku", "pridat do kosiku",
        "koupit", "rezervovat",
        "skladem na e-shopu", "skladem na eshopu",
    ]

    available = any(x in scope_low for x in good)

    return price, available


# ---------- KNIHY DOBROVSKÝ ----------
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
    text = safe_text(driver)

    price = price_from_element(driver, [
        "[class*='price']",
        "meta[itemprop='price']",
    ])
    if price is None:
        price = price_from_text("\n".join(text.splitlines()[:140]))
    if price is None:
        price = price_from_html(driver)

    t = text.lower()
    negatives = [
        "nedostupné", "nedostupne", "produkt je vyprodaný", "produkt je vyprodany",
        "vyprodáno", "vyprodano", "není skladem", "neni skladem",
        "předobjednávka", "predobjednavka",
    ]
    if any(x in t for x in negatives):
        return price, False

    # Na produktové stránce je dostupnost přímo u produktu; "Do košíku"
    # je silný signál, že lze objednat.
    available = any(x in t for x in [
        "do košíku", "do kosiku", "skladem na e-shopu", "skladem",
        "koupit"
    ])
    return price, available

FINDERS = {
    "SMARTY.CZ": smarty_find,
    "POKEMON4U.CZ": pokemon4u_find,
    "ALZA.CZ": alza_find,
    "GOOD-LUCK.CZ": goodluck_find,
    "POKEMALL.CZ": pokemall_find,
    "TCGSHOP.CZ": tcgshop_find,
    "LUXOR.CZ": luxor_find,
    "KNIHY-DOBROVSKY.CZ": knihy_dobrovsky_find,
}

CHECKERS = {
    "SMARTY.CZ": smarty_check,
    "POKEMON4U.CZ": pokemon4u_check,
    "ALZA.CZ": alza_check,
    "GOOD-LUCK.CZ": goodluck_check,
    "POKEMALL.CZ": pokemall_check,
    "TCGSHOP.CZ": tcgshop_check,
    "LUXOR.CZ": luxor_check,
    "KNIHY-DOBROVSKY.CZ": knihy_dobrovsky_check,
}


def normalize_product_text(text):
    text = (text or "").lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def special_match(name, url=""):
    """Rozpozná pouze 4 požadované speciální ETB."""
    t = normalize_product_text(f"{name} {url}")

    # 1) Mega Lucario ETB.
    # Některé obchody píší "Mega Lucario", jiné "Mega Evolutions ... Lucario".
    if (
        "elite trainer box" in t
        and (
            "mega lucario" in t
            or ("mega evolution" in t and "lucario" in t)
        )
    ):
        return 3500, "Mega Lucario – Elite Trainer Box"

    # 2) Základní Mega Evolution 01 ETB.
    # Důležité: obecné "Mega Evolution + ETB" nestačí, protože by chytalo
    # Pitch Black, Perfect Order, Gardevoir apod.
    if "elite trainer box" in t and (
        "mega evolution 01" in t
        or "mega evolutions 01" in t
    ):
        return 3500, "Mega Evolution 01 – Elite Trainer Box"

    # Alternativní zápis názvu základního produktu bez čísla.
    # Pouze pokud současně neobsahuje známé jiné Mega Evolution sety/pokémony.
    if "elite trainer box" in t and "mega evolution" in t:
        excluded = [
            "perfect order",
            "pitch black",
            "gardevoir",
            "lucario",
            "venusaur",
            "charizard",
            "blastoise",
            "greninja",
            "diancie",
            "marowak",
            "altaria",
            "ampharos",
            "manectric",
            "kangaskhan",
            "latias",
            "latios",
        ]
        if not any(x in t for x in excluded):
            return 3500, "Mega Evolution – Elite Trainer Box"

    # 3) Prismatic Evolutions.
    if "prismatic evolutions" in t and "elite trainer box" in t:
        return 3500, "Prismatic Evolutions – Elite Trainer Box"

    # 4) Ascended Heroes.
    if "ascended heroes" in t and "elite trainer box" in t:
        return 3500, "Ascended Heroes – Elite Trainer Box"

    return None, None

def check_store(driver, shop, state):
    url = STORES[shop]
    finder = FINDERS[shop]
    checker = CHECKERS[shop]

    print(f"\n===== {shop} =====")

    try:
        products = finder(driver)
    except Exception as e:
        print(f"❌ Chyba při hledání: {e}")
        return False

    print(f"Nalezeno ETB: {len(products)}")
    changed = False

    for product in products:
        if isinstance(product, dict):
            name = product.get("name", "")
            product_url = product.get("url", url)
        elif isinstance(product, (tuple, list)) and len(product) >= 2:
            name = str(product[0])
            product_url = str(product[1])
        else:
            name = str(product)
            product_url = url

        special_limit, special_name = special_match(name, product_url)

        if special_name:
            print(f"⭐ SPECIÁLNĚ SLEDUJI: {name}")
            print(f"   Rozpoznáno jako: {special_name} | Limit: {special_limit} Kč")
        else:
            print(f"   — mimo speciální seznam: {name}")
            continue

        try:
            price, available = checker(driver, name, product_url)
        except Exception as e:
            print(f"   ❌ Chyba kontroly: {e}")
            price, available = None, False

        print(f"   Cena: {price}")
        print(f"   Dostupnost: {available}")

        key = f"{shop}|{product_url}"
        old = state.get(key, {})

        # Kompatibilita se starším state_special.json.
        if isinstance(old, bool):
            old = {
                "qualifies": old,
                "last_alert_price": None
            }

        qualifies = (
            price is not None
            and price <= special_limit
            and available
        )

        last_alert_price = old.get("last_alert_price")

        if qualifies:
            if last_alert_price is None:
                discord_alert(
                    shop,
                    special_name,
                    price,
                    product_url,
                    reason="první splnění limitu"
                )
                state[key] = {
                    "qualifies": True,
                    "last_alert_price": price
                }
                changed = True
            elif price < last_alert_price:
                discord_alert(
                    shop,
                    special_name,
                    price,
                    product_url,
                    reason="pokles ceny",
                    old_price=last_alert_price
                )
                state[key] = {
                    "qualifies": True,
                    "last_alert_price": price
                }
                changed = True
            else:
                print("   ℹ️ Cena neklesla – upozornění neopakuji.")
        else:
            print("   Podmínky nesplněny.")
            if old.get("qualifies") or last_alert_price is not None:
                state[key] = {
                    "qualifies": False,
                    "last_alert_price": None
                }
                changed = True

    return changed

def main():
    print("======================================")
    print("       POKÉMON ETB HLÍDAČ - SPECIÁLNÍ")
    print("======================================")
    print(f"{len(STORES)} obchodů | speciální limit: 3500 Kč | plánovaná kontrola: 5 min")

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

        save_state(state)
        if changed:
            print("✅ Stav uložen do state_special.json")
        else:
            print("ℹ️ Stav se nezměnil, ale state_special.json byl uložen.")

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
