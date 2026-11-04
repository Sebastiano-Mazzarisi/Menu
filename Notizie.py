"""Notizie recenti di Putignano e dintorni in una scheda 1080x1350 stile Menu/Meteo.

Uso:
    python Notizie.py
    python Notizie.py --apri

L'immagine e il riepilogo JSON vengono salvati in dati/notizie/. Le fonti sono feed RSS
pubblici; il filtro geografico considera titolo e categorie, non il corpo dell'articolo.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dati" / "notizie"
LOGO = ROOT / "loghi" / "Logo-Menu.jpg"
if not LOGO.exists():
    LOGO = ROOT / "loghi" / "Menu.jpg"

TOWNS = {
    "Putignano": ("putignano",),
    "Castellana Grotte": ("castellana grotte", "castellana-grotte", "castellana g."),
    "Noci": ("noci",),
    "Alberobello": ("alberobello",),
    "Conversano": ("conversano",),
    "Turi": ("turi",),
    "Gioia del Colle": ("gioia del colle",),
    "Locorotondo": ("locorotondo",),
}

SOURCES = (
    {"name": "Informatissimo", "url": "https://www.informatissimo.net/?format=feed&type=rss"},
    {"name": "Faxonline", "url": "https://www.faxonline.it/feed/"},
    {"name": "Noci Gazzettino", "url": "https://www.nocigazzettino.it/feed/", "town": "Noci"},
    {"name": "PugliaNews24", "url": "https://www.puglianews24.eu/notizie/castellana-grotte/feed",
     "town": "Castellana Grotte"},
    {"name": "ViviCastellana", "url": "https://www.vivicastellanagrotte.it/index.php/notizietop",
     "town": "Castellana Grotte", "kind": "vivi"},
)


@dataclass
class News:
    title: str
    link: str
    published: datetime
    source: str
    town: str


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MenuRosticcerie/1.0 (+notizie locali)"})
    contexts = [ssl.create_default_context()]
    try:
        import certifi
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except ImportError:
        pass
    # Alcune testate locali hanno catene SSL non aggiornate. Il contenuto è pubblico e di sola lettura.
    contexts.append(ssl._create_unverified_context())
    last: Exception | None = None
    for context in contexts:
        try:
            with urllib.request.urlopen(request, timeout=30, context=context) as response:
                return response.read()
        except Exception as exc:
            last = exc
    raise RuntimeError(f"fonte non raggiungibile: {last}")


def parse_date(value: str | None) -> datetime:
    try:
        result = parsedate_to_datetime(value or "")
        return result.astimezone().replace(tzinfo=None)
    except Exception:
        return datetime.min


def find_town(text: str, fixed: str | None = None) -> str | None:
    if fixed:
        return fixed
    normalized = clean(text).casefold()
    for town, aliases in TOWNS.items():
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized) for alias in aliases):
            return town
    return None


def read_source(source: dict[str, str]) -> list[News]:
    raw = fetch(source["url"])
    if source.get("kind") == "vivi":
        page = raw.decode("utf-8", "replace")
        found: list[News] = []
        pattern = re.compile(r'<time\s+datetime="([^"]+)"[^>]*>.*?</time>.*?'
                             r'<h2[^>]*>\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
        for published, link, title in pattern.findall(page):
            try:
                when = datetime.fromisoformat(published).astimezone().replace(tzinfo=None)
            except Exception:
                when = datetime.min
            found.append(News(title=clean(title), link=urllib.parse.urljoin(source["url"], link),
                              published=when, source=source["name"], town=source["town"]))
        return found
    root = ET.fromstring(raw)
    found: list[News] = []
    for item in root.findall(".//item"):
        title = clean(item.findtext("title"))
        link = clean(item.findtext("link"))
        categories = " ".join(clean(node.text) for node in item.findall("category"))
        town = find_town(f"{title} {categories}", source.get("town"))
        if not title or not link or not town:
            continue
        found.append(News(title=title, link=link, published=parse_date(item.findtext("pubDate")),
                          source=source["name"], town=town))
    return found


def normalized_title(value: str) -> str:
    words = re.findall(r"[a-zà-ù0-9]+", value.casefold())
    return " ".join(word for word in words if word not in {"putignano", "noci", "castellana", "grotte"})


def collect(limit: int = 5) -> tuple[list[News], list[str]]:
    all_news: list[News] = []
    errors: list[str] = []
    for source in SOURCES:
        try:
            all_news.extend(read_source(source))
        except Exception as exc:
            errors.append(f"{source['name']}: {exc}")
    if not all_news:
        raise RuntimeError("nessuna fonte ha restituito notizie" + ("; " + "; ".join(errors) if errors else ""))

    now = datetime.now()
    recent = [item for item in all_news if item.published >= now - timedelta(days=10)]
    if len(recent) < limit:
        recent = [item for item in all_news if item.published >= now - timedelta(days=30)]
    recent.sort(key=lambda item: (item.town == "Putignano", item.published), reverse=True)

    chosen: list[News] = []
    towns: Counter[str] = Counter()
    seen: set[str] = set()
    for item in recent:
        key = normalized_title(item.title)
        if key in seen or towns[item.town] >= 2:
            continue
        chosen.append(item)
        seen.add(key)
        towns[item.town] += 1
        if len(chosen) == limit:
            break
    return chosen, errors


def news_icon() -> str:
    return '''<svg viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg">
<rect x="25" y="34" width="190" height="172" rx="32" fill="#fff"/>
<rect x="51" y="59" width="72" height="66" rx="12" fill="#e8921e"/>
<path d="M141 66h48M141 86h48M141 106h38M51 145h138M51 167h138" stroke="#1d7f46" stroke-width="13" stroke-linecap="round"/>
</svg>'''


def card(item: News, index: int) -> str:
    when = item.published.strftime("%d/%m · %H:%M") if item.published != datetime.min else ""
    return f'''<div class="card"><div class="num">{index}</div><div class="body">
<div class="meta"><b>{html.escape(item.town)}</b><span>{html.escape(item.source)} · {when}</span></div>
<div class="title D">{html.escape(item.title)}</div></div></div>'''


def page_html(items: list[News]) -> str:
    logo = LOGO.as_uri() if LOGO.exists() else ""
    sources = " · ".join(dict.fromkeys(item.source for item in items))
    return f'''<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,800&family=Atkinson+Hyperlegible:wght@400;700&display=swap">
<style>
*{{box-sizing:border-box}}html,body{{margin:0}}.D{{font-family:"Bricolage Grotesque","Segoe UI",sans-serif;font-weight:800}}
.c{{width:1080px;height:1350px;position:relative;overflow:hidden;padding:46px 44px;color:#1e1a16;
background:linear-gradient(180deg,#d7e9f4 0,#f4efe6 650px);font-family:"Atkinson Hyperlegible","Segoe UI",sans-serif}}
.hd{{display:flex;align-items:center;gap:24px}}.hd img{{width:120px;height:120px;border-radius:26px;background:#fff;object-fit:contain;box-shadow:0 4px 14px #0002}}
.kk{{font-size:30px;letter-spacing:.06em;text-transform:uppercase;color:#8a5306;font-weight:700}}
h1{{font-size:72px;line-height:1;margin:2px 0 0;letter-spacing:-.025em}}
.intro{{height:178px;display:flex;align-items:center;gap:28px;padding:8px 22px 0}}.ico{{width:150px;height:150px;filter:drop-shadow(0 7px 13px #0002)}}
.ico svg{{width:100%;height:100%}}.lead{{font-size:35px;line-height:1.15;color:#3e6653;font-weight:700}}
.list{{display:grid;gap:14px}}.card{{min-height:158px;background:#fff;border-radius:25px;padding:19px 24px;display:flex;gap:20px;align-items:flex-start;box-shadow:0 4px 13px #3c280f1c}}
.num{{width:54px;height:54px;border-radius:16px;background:#1d7f46;color:#fff;display:grid;place-items:center;flex:none;font:800 30px "Bricolage Grotesque","Segoe UI",sans-serif}}
.body{{min-width:0}}.meta{{display:flex;align-items:baseline;gap:14px;margin-bottom:6px}}.meta b{{font-size:25px;color:#15713f}}.meta span{{font-size:21px;color:#8f7e6d}}
.title{{font-size:34px;line-height:1.08;letter-spacing:-.01em}}.ft{{position:absolute;left:44px;right:44px;bottom:28px;text-align:center;font-size:22px;color:#8f857a}}
</style></head><body><div class="c"><div class="hd"><img src="{logo}"><div><div class="kk">Notizie · Putignano e dintorni</div><h1 class="D">Oggi nel territorio</h1></div></div>
<div class="intro"><div class="ico">{news_icon()}</div><div class="lead">Una selezione automatica delle notizie locali più recenti</div></div>
<div class="list">{''.join(card(item, index) for index, item in enumerate(items, 1))}</div>
<div class="ft">Fonti: {html.escape(sources)} · aggiornato alle {datetime.now():%H:%M}</div></div></body></html>'''


def render(items: list[News], target: Path) -> Path:
    page = target.with_suffix(".html")
    page.write_text(page_html(items), encoding="utf-8")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            tab = browser.new_page(viewport={"width": 1080, "height": 1350})
            tab.goto(page.as_uri(), wait_until="load", timeout=60000)
            try:
                tab.evaluate("document.fonts.ready.then(() => true)")
            except Exception:
                pass
            tab.wait_for_timeout(300)
            tab.screenshot(path=str(target), type="jpeg", quality=91)
        finally:
            browser.close()
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea la schermata delle notizie di Putignano e dintorni.")
    parser.add_argument("--apri", action="store_true", help="Apre l'immagine dopo averla creata.")
    parser.add_argument("--numero", type=int, default=5, choices=range(3, 7), metavar="3-6")
    args = parser.parse_args()
    items, errors = collect(args.numero)
    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / datetime.now().strftime("%Y-%m-%d")
    image = render(items, stem.with_suffix(".jpg"))
    payload = {"aggiornato": datetime.now().isoformat(timespec="seconds"),
               "notizie": [{**asdict(item), "published": item.published.isoformat()} for item in items],
               "errori": errors}
    stem.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Notizie create: {image} ({len(items)} articoli)")
    for item in items:
        print(f"- {item.town}: {item.title} [{item.source}]")
    if errors:
        print("Fonti non raggiunte: " + " | ".join(errors))
    if args.apri:
        import os
        os.startfile(image)


if __name__ == "__main__":
    main()
