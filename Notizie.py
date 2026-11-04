"""Notizie recenti di Putignano e dintorni in una scheda 1080x1350 stile Menu/Meteo.

Uso:
    python Notizie.py
    python Notizie.py --apri

L'immagine e il riepilogo JSON vengono salvati in dati/notizie/. Le fonti sono feed RSS
pubblici; il filtro geografico considera titolo e categorie, non il corpo dell'articolo.
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import math
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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
    {"name": "InfoAlert Putignano", "url": "https://putignano.infoalert365.it/feed/",
     "town": "Putignano", "authority": 1.0},
    {"name": "Informatissimo", "url": "https://www.informatissimo.net/?format=feed&type=rss",
     "authority": 0.82},
    {"name": "Faxonline", "url": "https://www.faxonline.it/feed/", "authority": 0.82},
    {"name": "TRM", "url": "https://www.trmtv.it/feed", "authority": 0.78},
    {"name": "Noci Gazzettino", "url": "https://www.nocigazzettino.it/feed/", "town": "Noci",
     "authority": 0.74},
    {"name": "PugliaNews24", "url": "https://www.puglianews24.eu/notizie/castellana-grotte/feed",
     "town": "Castellana Grotte"},
    {"name": "ViviCastellana", "url": "https://www.vivicastellanagrotte.it/index.php/notizietop",
     "town": "Castellana Grotte", "kind": "vivi"},
)

STOP_WORDS = {
    "a", "ad", "al", "alla", "alle", "allo", "anche", "che", "con", "da", "dal", "dalla",
    "delle", "di", "e", "gli", "i", "il", "in", "la", "le", "lo", "nel", "nella", "per",
    "su", "tra", "un", "una", "uno", "putignano", "noci", "castellana", "grotte", "alberobello",
}

# Le parole sono divise per peso: prima la sicurezza e i servizi che incidono subito sulla vita locale,
# poi amministrazione/economia e infine eventi e costume.
INTEREST_TERMS = (
    (18, ("allerta", "emergenza", "incendio", "incidente", "arrest", "omicidio", "rapina",
          "scompar", "evacu", "maltempo", "chiusura", "interruzione")),
    (14, ("ordinanza", "viabil", "traffico", "scuol", "ospedale", "sanità", "acqua", "rifiuti",
          "ferrovia", "treno", "autobus", "lavori pubblici", "protezione civile")),
    (10, ("comune", "consiglio comunale", "bando", "contribut", "lavoro", "imprese", "tari",
          "bilancio", "elezioni", "ambiente")),
    (6, ("evento", "festival", "carnevale", "concerto", "spettacolo", "mostra", "sagra", "sport")),
)

LOW_VALUE_TERMS = ("oroscopo", "necrolog", "auguri", "buon compleanno", "foto del giorno")


@dataclass
class News:
    title: str
    link: str
    published: datetime
    source: str
    town: str
    summary: str = ""
    category: str = "altro"
    score: float = 0.0
    duplicate_sources: tuple[str, ...] = ()


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
        summary = clean(item.findtext("description"))
        # Molti feed (in particolare Faxonline) indicano il comune soltanto nell'URL.
        town = find_town(f"{title} {categories} {link} {summary[:400]}", source.get("town"))
        if not title or not link or not town:
            continue
        found.append(News(title=title, link=link, published=parse_date(item.findtext("pubDate")),
                          source=source["name"], town=town, summary=summary[:1200]))
    return found


def title_tokens(value: str) -> set[str]:
    words = re.findall(r"[a-zà-ù0-9]+", value.casefold())
    return {word for word in words if len(word) > 2 and word not in STOP_WORDS}


def normalized_title(value: str) -> str:
    return " ".join(sorted(title_tokens(value)))


def same_story(left: News, right: News) -> bool:
    """Riconosce lo stesso fatto anche quando le testate usano titoli diversi."""
    if left.link.rstrip("/") == right.link.rstrip("/"):
        return True
    a, b = title_tokens(left.title), title_tokens(right.title)
    if not a or not b:
        return False
    overlap = len(a & b) / len(a | b)
    sequence = difflib.SequenceMatcher(None, normalized_title(left.title),
                                       normalized_title(right.title)).ratio()
    # Titoli molto corti richiedono un accordo più forte per evitare falsi positivi.
    shared = len(a & b)
    return overlap >= 0.52 or sequence >= 0.80 or (shared >= 4 and overlap >= 0.38)


def classify(item: News) -> str:
    text = f"{item.title} {item.summary}".casefold()
    groups = (
        ("sicurezza", ("allerta", "incend", "incident", "arrest", "omicid", "rapina", "maltempo")),
        ("servizi", ("viabil", "traffico", "scuol", "osped", "sanità", "rifiuti", "acqua", "treno")),
        ("amministrazione", ("comune", "sindac", "consiglio", "ordinanza", "bando", "bilancio")),
        ("economia", ("lavoro", "impres", "commerc", "turismo", "agricol")),
        ("eventi", ("evento", "festival", "carnevale", "concerto", "spettacolo", "mostra", "sagra")),
        ("sport", ("sport", "partita", "campionato", "pallavolo", "calcio", "pallamano")),
    )
    for category, terms in groups:
        if any(term in text for term in terms):
            return category
    return "altro"


def interest_score(item: News, now: datetime) -> float:
    text = f"{item.title} {item.summary[:700]}".casefold()
    age_hours = max(0.0, (now - item.published).total_seconds() / 3600) if item.published != datetime.min else 720
    # La freschezza conta molto: dopo due giorni il vantaggio temporale si riduce nettamente.
    score = 30 * math.exp(-age_hours / 48)
    score += 25 if item.town == "Putignano" else 9
    source = next((source for source in SOURCES if source["name"] == item.source), {})
    score += 10 * float(source.get("authority", 0.65))
    for weight, terms in INTEREST_TERMS:
        matches = sum(1 for term in terms if term in text)
        if matches:
            score += weight + min(matches - 1, 2) * 2
    if any(term in text for term in LOW_VALUE_TERMS):
        score -= 18
    # Un titolo concreto, con numeri o una località esplicita, è in genere più informativo.
    if re.search(r"\d", item.title):
        score += 2
    if item.town.casefold().split()[0] in item.title.casefold():
        score += 3
    return round(score, 2)


def is_obsolete(item: News, now: datetime) -> bool:
    """Esclude contenuti che non sono più utili, soprattutto le allerte a rapida scadenza."""
    if item.published == datetime.min:
        return True
    age_hours = max(0.0, (now - item.published).total_seconds() / 3600)
    text = f"{item.title} {item.summary[:300]}".casefold()
    weather_alert = any(term in text for term in (
        "allerta", "situazione meteo", "bollettino meteo", "previsioni meteo", "rischio meteo"
    )) and any(term in text for term in (
        "meteo", "temporali", "vento", "idrogeolog", "arancione", "gialla", "rossa", "protezione civile"
    ))
    # Le allerte vengono normalmente emesse il pomeriggio per il giorno seguente: 30 ore consentono
    # di mostrarle durante la loro validità, ma non nella rassegna del mattino successivo.
    return weather_alert and age_hours > 30


def merge_duplicates(items: list[News], now: datetime) -> list[News]:
    clusters: list[list[News]] = []
    for item in sorted(items, key=lambda value: value.published, reverse=True):
        cluster = next((group for group in clusters if any(same_story(item, other) for other in group)), None)
        if cluster is None:
            clusters.append([item])
        else:
            cluster.append(item)

    merged: list[News] = []
    for cluster in clusters:
        for item in cluster:
            item.category = classify(item)
            item.score = interest_score(item, now)
        representative = max(cluster, key=lambda value: (value.score, len(value.summary), value.published))
        sources = tuple(dict.fromkeys(value.source for value in cluster))
        representative.duplicate_sources = sources
        # Conferme indipendenti aumentano l'affidabilità e l'interesse, senza creare doppioni.
        representative.score = round(representative.score + min(8, (len(sources) - 1) * 4), 2)
        merged.append(representative)
    return merged


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
    all_news = [item for item in all_news if not is_obsolete(item, now)]
    recent = [item for item in all_news if item.published >= now - timedelta(days=4)]
    if len(recent) < limit:
        recent = [item for item in all_news if item.published >= now - timedelta(days=7)]
    ranked = sorted(merge_duplicates(recent, now), key=lambda item: (item.score, item.published), reverse=True)

    chosen: list[News] = []
    for item in ranked:
        # Non più di tre notizie dello stesso comune e due dello stesso argomento nella scheda.
        town_cap = 3 if item.town == "Putignano" else 2
        if sum(value.town == item.town for value in chosen) >= town_cap:
            continue
        if sum(value.category == item.category for value in chosen) >= 2:
            continue
        chosen.append(item)
        if len(chosen) == limit:
            break
    # Se i vincoli di varietà sono troppo stretti, completa comunque il numero richiesto.
    if len(chosen) < limit:
        remaining = [item for item in ranked if item not in chosen]
        chosen.extend(remaining[:limit - len(chosen)])
    return chosen, errors


def news_icon() -> str:
    return '''<svg viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg">
<rect x="25" y="34" width="190" height="172" rx="32" fill="#fff"/>
<rect x="51" y="59" width="72" height="66" rx="12" fill="#e8921e"/>
<path d="M141 66h48M141 86h48M141 106h38M51 145h138M51 167h138" stroke="#1d7f46" stroke-width="13" stroke-linecap="round"/>
</svg>'''


def card(item: News, index: int) -> str:
    when = item.published.strftime("%d/%m · %H:%M") if item.published != datetime.min else ""
    size_class = " xlong" if len(item.title) > 105 else (" long" if len(item.title) > 72 else "")
    sources = " + ".join(item.duplicate_sources) if len(item.duplicate_sources) > 1 else item.source
    return f'''<div class="card"><div class="num">{index}</div><div class="body">
<div class="meta"><b>{html.escape(item.town)}</b><span>{html.escape(sources)} · {when}</span></div>
<div class="title D{size_class}">{html.escape(item.title)}</div></div></div>'''


def page_html(items: list[News]) -> str:
    logo = LOGO.as_uri() if LOGO.exists() else ""
    sources = " · ".join(dict.fromkeys(item.source for item in items))
    weekdays = ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica")
    months = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
              "settembre", "ottobre", "novembre", "dicembre")
    today = datetime.now()
    date_title = f"{weekdays[today.weekday()]} {today.day} {months[today.month - 1]}"
    return f'''<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,800&family=Atkinson+Hyperlegible:wght@400;700&display=swap">
<style>
*{{box-sizing:border-box}}html,body{{margin:0}}.D{{font-family:"Bricolage Grotesque","Segoe UI",sans-serif;font-weight:800}}
.c{{width:1080px;height:1350px;position:relative;overflow:hidden;padding:46px 44px;color:#1e1a16;
background:linear-gradient(180deg,#dce2e9 0,#f4efe6 640px);font-family:"Atkinson Hyperlegible","Segoe UI",sans-serif}}
.hd{{display:flex;align-items:center;gap:24px}}.hd img{{width:120px;height:120px;border-radius:26px;background:#fff;object-fit:contain;box-shadow:0 4px 14px #0002}}
.kk{{font-size:32px;letter-spacing:.06em;text-transform:uppercase;color:#8a5306;font-weight:700}}
h1{{font-size:68px;line-height:1;margin:2px 0 0;letter-spacing:-.025em}}
.strap{{height:100px;margin:34px 0 20px;background:#1d7f46;color:#fff;border-radius:26px;padding:15px 26px;display:flex;align-items:center;gap:20px;font-size:38px;font-weight:700}}
.strap .ico{{width:70px;height:70px;flex:none}}.strap .ico svg{{width:100%;height:100%}}
.list{{display:grid;gap:14px}}.card{{height:178px;background:#fff;border-radius:25px;padding:20px 24px;display:flex;gap:20px;align-items:flex-start;box-shadow:0 4px 13px #3c280f1c}}
.num{{width:58px;height:58px;border-radius:17px;background:#1d7f46;color:#fff;display:grid;place-items:center;flex:none;font:800 32px "Bricolage Grotesque","Segoe UI",sans-serif}}
.body{{min-width:0}}.meta{{display:flex;align-items:baseline;gap:14px;margin-bottom:7px;white-space:nowrap;overflow:hidden}}
.meta b{{font-size:27px;color:#15713f}}.meta span{{font-size:23px;color:#7d7165;overflow:hidden;text-overflow:ellipsis}}
.title{{font-size:38px;line-height:1.04;letter-spacing:-.012em;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}}
.title.long{{font-size:35px}}.title.xlong{{font-size:32px;line-height:1.03}}
.ft{{position:absolute;left:44px;right:44px;bottom:25px;text-align:center;font-size:23px;color:#8f857a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
</style></head><body><div class="c"><div class="hd"><img src="{logo}"><div><div class="kk">Notizie a Putignano e dintorni</div><h1 class="D">{html.escape(date_title)}</h1></div></div>
<div class="strap"><div class="ico">{news_icon()}</div><span>Le {len(items)} notizie locali da sapere</span></div>
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


def create(limit: int = 5) -> tuple[Path, list[News], list[str]]:
    """Crea immagine e JSON; è richiamabile sia da riga di comando sia da Menu.py."""
    items, errors = collect(limit)
    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / datetime.now().strftime("%Y-%m-%d")
    image = render(items, stem.with_suffix(".jpg"))
    payload = {"aggiornato": datetime.now().isoformat(timespec="seconds"),
               "notizie": [{**asdict(item), "published": item.published.isoformat()} for item in items],
               "errori": errors}
    stem.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return image, items, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea la schermata delle notizie di Putignano e dintorni.")
    parser.add_argument("--apri", action="store_true", help="Apre l'immagine dopo averla creata.")
    parser.add_argument("--numero", type=int, default=5, choices=range(3, 7), metavar="3-6")
    args = parser.parse_args()
    image, items, errors = create(args.numero)
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
