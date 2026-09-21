import os, re, json, time
from collections import Counter
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

MAX_PRICE = 2500
STATE_FILE = "state_alza_xzone.json"
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
TOKEN = os.getenv("DISCORD_TOKEN")

ALZA_URL = "https://m.alza.cz/hracky/pokemon-booster-boxy-a-specialni-boxy/18903046.htm"
XZONE_URL = "https://www.xzone.cz/pokemon-tcg-elite-trainer-boxy"

def make_driver():
    o = Options()
    for arg in ["--headless=new","--no-sandbox","--disable-dev-shm-usage",
                "--window-size=1920,1080","--lang=cs-CZ","--disable-notifications"]:
        o.add_argument(arg)
    o.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
    return webdriver.Chrome(options=o)

def normalize_url(url):
    return (url or "").split("#")[0].rstrip("/")

def safe_text(driver):
    try: return driver.find_element(By.TAG_NAME, "body").text
    except Exception: return ""

def parse_price(text):
    vals=[]
    for pat in [r"(?<!\d)(\d{1,2}(?:[\s\u00A0]\d{3})+)\s*(?:Kč|-)",
                r"(?<!\d)(\d{3,5})\s*(?:Kč|-)"]:
        for raw in re.findall(pat,text,re.I):
            v=int(re.sub(r"[^0-9]","",raw))
            if 100<=v<=99999: vals.append(v)
    return Counter(vals).most_common(1)[0][0] if vals else None

def discord_alert(shop,name,price,url):
    payload={"content":f"🚨 **{shop} – ETB SKLADEM!**\n**{name}**\n💰 **{price:,} Kč**\n🔗 {url}".replace(",", " ")}
    r=requests.post(f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
                    headers={"Authorization":f"Bot {TOKEN}"},json=payload,timeout=20)
    print("Discord:",r.status_code)
    if r.status_code not in (200,201): print(r.text[:500])

def load_state():
    try:
        with open(STATE_FILE,encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_state(s):
    with open(STATE_FILE,"w",encoding="utf-8") as f: json.dump(s,f,ensure_ascii=False,indent=2)

def alza_find(driver):
    driver.get(ALZA_URL)
    time.sleep(3)
    out=[]; seen=set()
    for a in driver.find_elements(By.TAG_NAME,"a"):
        try:
            href=normalize_url(a.get_attribute("href")); name=a.text.strip()
            low=(href+" "+name).lower()
            if href and href not in seen and "alza.cz" in low and "elite-trainer-box" in low and "/18903047" not in href:
                seen.add(href); out.append((name or href.rsplit("/",1)[-1],href))
        except Exception: pass
    if not out:
        print("ALZA DEBUG URL:", driver.current_url)
        print("ALZA DEBUG TITLE:", driver.title)
        print("ALZA DEBUG BODY:", safe_text(driver).replace("\n", " ")[:500])
    return out

def alza_check(driver,name,url):
    driver.get(url); time.sleep(.8); text=safe_text(driver); low=text.lower()
    price=parse_price(text)
    if any(x in low for x in ["momentálně nedostupné","momentálně nedostupne","není skladem","neni skladem","vyprodáno","vyprodano","discontinued"]):
        return price,False
    return price,any(x in low for x in ["skladem","do košíku","do kosiku","koupit","objednat"])

def xzone_find(driver):
    # Xzone category page is unreliable in GitHub Actions.
    # Use known direct ETB product URLs instead.
    urls = [
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-mega-evolution-elite-trainer-box-mega-lucario",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-scarlet-violet-black-bolt-elite-trainer-box",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-celebrations-elite-trainer-box",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-shining-fates-elite-trainer-box",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-journey-together-elite-trainer-box",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-champions-path-elite-trainer-box",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-stellar-crown-elite-trainer-box",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-obsidian-flames-elite-trainer-box",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-crown-zenith-elite-trainer-box",
        "https://www.xzone.cz/karetni-hra-pokemon-tcg-ascended-heroes-elite-trainer-box",
    ]

    result = []
    for url in urls:
        try:
            driver.get(url)
            time.sleep(0.8)
            try:
                name = driver.find_element(By.TAG_NAME, "h1").text.strip()
            except Exception:
                name = url.rstrip("/").split("/")[-1]

            if "elite trainer box" in (name + " " + url).lower():
                result.append((name, url))
        except Exception:
            pass

    return result

def xzone_check(driver, name, url):
    driver.get(url)
    time.sleep(1.0)

    text = safe_text(driver)
    low = text.lower()

    # Xzone shows the real product price as e.g. "1 990 Kč".
    # Do not use generic 3-digit numbers such as product codes or credits.
    prices = []
    for m in re.finditer(
        r'(?<!\d)(\d{1,2}(?:[\s\u00a0]\d{3})|\d{3,5})\s*Kč',
        text,
        re.I
    ):
        raw = m.group(1).replace(" ", "").replace("\u00a0", "")
        try:
            prices.append(int(raw))
        except Exception:
            pass

    price = None
    if prices:
        realistic = [p for p in prices if 300 <= p <= 100000]
        if realistic:
            price = realistic[0]

    # Fallback: structured product price.
    if price is None:
        for selector in [
            "meta[itemprop='price']",
            "[itemprop='price']",
            "meta[property='product:price:amount']",
        ]:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, selector):
                    raw = el.get_attribute("content") or el.text
                    m = re.search(r'\d+(?:[.,]\d+)?', raw or "")
                    if m:
                        p = float(m.group(0).replace(",", "."))
                        if p >= 300:
                            price = int(p)
                            break
                if price is not None:
                    break
            except Exception:
                pass

    unavailable = any(x in low for x in [
        "není skladem", "neni skladem",
        "vyprodáno", "vyprodano",
        "momentálně nedostupné", "momentalne nedostupne",
        "nedostupné", "nedostupne",
    ])

    available = False
    if not unavailable:
        available = any(x in low for x in [
            "do košíku", "do kosiku",
            "koupit", "objednat",
        ])

    return price, available

def run_shop(driver,state,shop,finder,checker):
    print(f"\n===== {shop} ====="); links=finder(driver); print("Nalezeno ETB:",len(links)); changed=False
    for name,url in links:
        price,available=checker(driver,name,url); qualifies=price is not None and price<=MAX_PRICE and available
        key=f"{shop}|{url}"; previous=state.get(key,False)
        print(f"\nKontroluji: {name}\nCena: {price}\nDostupnost: {available}")
        if qualifies:
            print("🚨 PODMÍNKY SPLNĚNY!")
            if not previous: discord_alert(shop,name,price,url); print("✅ Upozornění odesláno.")
            else: print("ℹ️ Upozornění už bylo odesláno, neopakuji.")
        else: print("Podmínky nesplněny.")
        if previous!=qualifies: state[key]=qualifies; changed=True
    return changed

def main():
    print("="*55); print("       ALZA + XZONE ETB HLÍDAČ - SAMOSTATNĚ"); print("="*55); print(f"Limit: {MAX_PRICE} Kč"); print("="*55)
    state=load_state(); driver=make_driver()
    try:
        changed=False
        try: changed |= run_shop(driver,state,"ALZA.CZ",alza_find,alza_check)
        except Exception as e: print("ALZA CHYBA:",repr(e))
        try: changed |= run_shop(driver,state,"XZONE.CZ",xzone_find,xzone_check)
        except Exception as e: print("XZONE CHYBA:",repr(e))
        if changed: save_state(state)
        else: print("\nℹ️ Stav se nezměnil.")
    finally: driver.quit()

if __name__=="__main__": main()
