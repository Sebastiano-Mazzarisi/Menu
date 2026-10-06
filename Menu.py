from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "locali.json"
INPUT = ROOT / "ingresso"
CURRENT = ROOT / "menu"
ARCHIVE = ROOT / "archivio"
LOGOS = ROOT / "loghi"
PROFILE = ROOT / "profilo"
ERRORS = ROOT / "errori"
DATA = ROOT / "dati"
STATE = DATA / "stato.json"
OUTPUT = ROOT / "Menu.html"
WEB_PAGE = ROOT / "index.html"  # pagina pubblicata su GitHub Pages (cellulare)
WEB_URL = "https://sebastiano-mazzarisi.github.io/Menu/"
ICONS = ROOT / "icone"
APP_LOGO = LOGOS / "Menu.jpg"
ICON_SIZES = {"icona-512.png": 512, "icona-192.png": 192, "icona-180.png": 180, "favicon.png": 64}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
TEXT_EXTENSION = ".json"  # menu testuali letti da un sito (es. Pane & Co)
MENU_EXTENSIONS = IMAGE_EXTENSIONS | {TEXT_EXTENSION}
MONTHS = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
          "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}


def slugify(value: str) -> str:
    clean = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-") or "locale"


def make_folders() -> None:
    for folder in (INPUT, CURRENT, ARCHIVE, LOGOS, PROFILE, ERRORS, DATA):
        folder.mkdir(parents=True, exist_ok=True)
    # le schermate di diagnostica servono solo per pochi giorni: tolgo quelle più vecchie di 2 giorni
    limit = datetime.now().timestamp() - 2 * 86400
    for item in ERRORS.glob("*.png"):
        try:
            if item.stat().st_mtime < limit:
                item.unlink()
        except OSError:
            pass


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def image_date(path: Path) -> date:
    patterns = (r"(20\d{2})[-_](\d{2})[-_](\d{2})", r"(\d{2})[-_](\d{2})[-_](20\d{2})")
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, path.stem)
        if match:
            parts = [int(piece) for piece in match.groups()]
            try:
                return date(parts[0], parts[1], parts[2]) if index == 0 else date(parts[2], parts[1], parts[0])
            except ValueError:
                pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def newest_image(folder: Path) -> Path | None:
    images = [item for item in folder.glob("*") if item.is_file() and item.suffix.lower() in MENU_EXTENSIONS]
    return max(images, key=lambda item: (image_date(item), item.stat().st_mtime), default=None)


def copy_current(shop: dict[str, Any], source: Path, menu_day: date) -> Path:
    extension = source.suffix.lower() if source.suffix.lower() in MENU_EXTENSIONS else ".jpg"
    for old in CURRENT.glob(f"{shop['id']}.*"):
        if old.suffix.lower() != extension and old.suffix.lower() in MENU_EXTENSIONS:
            old.unlink()
    target = CURRENT / f"{shop['id']}{extension}"
    changed = not target.exists() or file_hash(target) != file_hash(source)
    if changed:
        shutil.copy2(source, target)
        archive_folder = ARCHIVE / shop["id"]
        archive_folder.mkdir(parents=True, exist_ok=True)
        archive = archive_folder / f"{menu_day.isoformat()}_{datetime.now():%H%M%S}{extension}"
        shutil.copy2(source, archive)
    return target


def remove_current(shop: dict[str, Any]) -> None:
    """Rimuove soltanto le copie generate; ingresso e archivio restano intatti."""
    for candidate in CURRENT.glob(f"{shop['id']}.*"):
        if candidate.is_file() and candidate.suffix.lower() in MENU_EXTENSIONS:
            candidate.unlink()


@dataclass
class Result:
    image: Path | None
    menu_day: date | None
    source: str
    checked_at: str
    error: str = ""


EXPAND_JS = """(selector) => {
 const root = document.querySelector(selector);
 if (!root) return false;
 const more = [...root.querySelectorAll('[role=button], span, div')].find(el =>
   /^(altro|mostra altro|see more)\\s*(\\.\\.\\.|…)?$/i.test((el.textContent || '').trim()) && el.children.length <= 1);
 if (!more) return false;
 more.click();
 return true;
}"""


def expand_more(page: Any, selector: str) -> None:
    """Apre "Altro..." / "See more" finché il testo del post è completo (i menu lunghi
    Facebook li divide in più parti). Il clic è fatto da JavaScript, così non lo blocca
    nessuna finestra sovrapposta."""
    for _ in range(4):
        try:
            if not page.evaluate(EXPAND_JS, selector):
                return
        except Exception:
            return
        page.wait_for_timeout(900)


def clean_post_text(text: str) -> str:
    """Toglie i comandi di Facebook rimasti in fondo al testo: "… Altro...", "Vedi meno", "See less"."""
    return re.sub(r"\s*(…|\.\.\.)?\s*(altro|mostra altro|see more|vedi meno|mostra meno|see less)\s*(\.\.\.|…)?\s*$",
                  "", text or "", flags=re.IGNORECASE).strip()


def dismiss_dialogs(page: Any) -> None:
    """Chiude le finestre che coprono la pagina: cookie di Facebook/Instagram (rifiuta quelli
    facoltativi) e l'invito ad accedere. Senza questo i post restano nascosti dietro."""
    for label in ("Rifiuta cookie facoltativi", "Decline optional cookies", "Rifiuta i cookie facoltativi",
                  "Consenti solo i cookie essenziali", "Only allow essential cookies"):
        try:
            button = page.get_by_role("button", name=label).first
            if button.is_visible(timeout=500):
                button.click(timeout=2000)
                page.wait_for_timeout(1500)
                break
        except Exception:
            continue
    for selector in ("div[role='dialog'] [aria-label='Chiudi']", "div[role='dialog'] [aria-label='Close']"):
        try:
            close = page.locator(selector).first
            if close.is_visible(timeout=500):
                close.click(timeout=2000)
                page.wait_for_timeout(1000)
        except Exception:
            continue


# foto grande al centro del visualizzatore delle storie (non avatar né miniature laterali)
STORY_IMAGE_JS = """() => {
 const imgs = [...document.querySelectorAll('img')].filter(img => {
   const r = img.getBoundingClientRect(); const s = getComputedStyle(img);
   return img.complete && img.naturalWidth >= 200 && img.naturalHeight >= 200 && r.width >= 200
     && r.height >= 200 && s.visibility !== 'hidden' && s.display !== 'none'
     && r.right > innerWidth * .35 && r.left < innerWidth * .75 && r.top < innerHeight && r.bottom > 0;
 });
 imgs.sort((a, b) => { const x = a.getBoundingClientRect(), y = b.getBoundingClientRect();
   return y.width * y.height - x.width * x.height; });
 return imgs.length ? {src: imgs[0].currentSrc || imgs[0].src, alt: imgs[0].alt || '',
   w: imgs[0].naturalWidth, h: imgs[0].naturalHeight} : null;
}"""
# Facebook mostra prima un'anteprima sfocata e piccola (es. 320x240) e poi la foto vera:
# si accetta solo una foto con il lato minore di almeno STORY_MIN_SIDE pixel
STORY_MIN_SIDE = 400
# "1 h", "5 min", "adesso" accanto al nome in alto nel visualizzatore = storia delle ultime ore
STORY_RECENT_JS = """() => {
 const re = /(^|[^\\p{L}\\p{N}])(\\d{1,2}\\s*(m|min|h)|adesso|ora)(?![\\p{L}\\p{N}])/iu;
 return [...document.querySelectorAll('*')].some(el => {
   const t = (el.innerText || el.textContent || '').trim();
   if (!t || t.length > 80 || !re.test(t)) return false;
   const r = el.getBoundingClientRect();
   return r.width > 0 && r.height > 0 && r.height < 60 && r.top >= 0 && r.top < 140
     && r.left > innerWidth * .35 && r.left < innerWidth;
 });
}"""


class BrowserCollector:
    def __init__(self, visible: bool) -> None:
        self.visible = visible
        self.playwright = None
        self.context = None
        self.profile_key = ""
        self.last_text = ""
        self.last_alt = ""

    def start(self, source: dict[str, Any] | None = None) -> None:
        source = source or {}
        configured_profile = source.get("profilo", "")
        profile = Path(os.path.expandvars(configured_profile)) if configured_profile else PROFILE
        profile_key = str(profile.resolve())
        if self.context and self.profile_key == profile_key:
            return
        if self.context:
            self.stop()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright non installato: pip install -r requirements.txt") from exc
        self.playwright = sync_playwright().start()
        offscreen = bool(source.get("fuori_schermo")) and not self.visible
        options: dict[str, Any] = {
            # "fuori_schermo": Chrome vero (non headless, che Facebook tratta diversamente per le
            # storie) ma con la finestra spostata fuori dallo schermo: non si vede nulla
            "headless": not self.visible and not offscreen,
            "viewport": {"width": 1280, "height": 900},
            "locale": "it-IT",
        }
        if offscreen:
            options["args"] = ["--window-position=-32000,-32000"]
        if source.get("canale"):
            options["channel"] = source["canale"]
        self.context = self.playwright.chromium.launch_persistent_context(str(profile), **options)
        self.profile_key = profile_key

    def stop(self) -> None:
        if self.context:
            self.context.close()
            self.context = None
        if self.playwright:
            self.playwright.stop()
            self.playwright = None
        self.profile_key = ""

    def login(self) -> None:
        self.start()
        page = self.context.pages[0] if self.context.pages else self.context.new_page()
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
        page = self.context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
        input("Accedi nei due siti, poi torna qui e premi INVIO...")

    def capture(self, shop: dict[str, Any], source: dict[str, Any]) -> Path | None:
        self.start(source)
        page = self.context.new_page()
        try:
            page.goto(source["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(int(source.get("attesa_secondi", 6)) * 1000)
            dismiss_dialogs(page)
            if source.get("apri_storia"):
                opened = False
                if source.get("utente"):
                    try:
                        avatar = page.locator(
                            f'img[alt*="profilo di {source["utente"]}" i]'
                        ).first
                        avatar.locator("..").click(timeout=5000)
                        page.wait_for_timeout(5000)
                        opened = "/stories/" in page.url and "/highlights/" not in page.url
                    except Exception:
                        pass
                for selector in source.get(
                    "selettori_apertura",
                    ["a[href*='/stories/']:not([href*='/highlights/'])", "header img"],
                ):
                    if opened:
                        break
                    try:
                        candidate = page.locator(selector).first
                        if candidate.is_visible(timeout=1500):
                            candidate.click(timeout=3000)
                            page.wait_for_timeout(5000)
                            opened = "/stories/" in page.url
                            if opened:
                                break
                    except Exception:
                        continue
                if not opened:
                    raise RuntimeError("nessuna Storia pubblicata in questo momento (o non si è aperta)")
                if source.get("richiede_recente"):
                    visible_text = page.locator("body").inner_text(timeout=5000)
                    recent = re.search(
                        r"\b(?:\d{1,2}\s*(?:m|min|h|ora|ore)|adesso)\b",
                        visible_text,
                        re.IGNORECASE,
                    )
                    if not recent:
                        raise RuntimeError("la Storia non risulta pubblicata nelle ultime 24 ore")
            selector = source.get("selettore", "")
            if selector and source.get("scorri"):
                # Facebook carica i post solo scorrendo la pagina
                for _ in range(int(source.get("scorri_max", 12))):
                    if page.locator(selector).count():
                        break
                    page.mouse.wheel(0, 1200)
                    page.wait_for_timeout(1500)
            self.last_text = ""
            if source.get("selettore_testo"):
                expand_more(page, source["selettore_testo"])
                try:
                    self.last_text = clean_post_text(
                        page.locator(source["selettore_testo"]).first.inner_text(timeout=3000))
                except Exception:
                    pass
            if selector and not page.locator(selector).count():
                if self.last_text.strip():
                    self.last_alt = ""
                    return None  # post solo testo (es. menu scritto nel post): lo gestisce acquire
                raise RuntimeError(f"nessun elemento trovato con il selettore {selector}")
            target = page.locator(selector).first if selector else self._largest_media(page)
            if target is None:
                raise RuntimeError("nessuna immagine grande visibile")
            # Facebook scrive nel testo alternativo dell'immagine le parole che legge nella foto
            # (es. "...il seguente testo: 'Siamo chiusi da domenica 4/10 a mercoledì 7/10'")
            try:
                self.last_alt = target.get_attribute("alt", timeout=2000) or ""
            except Exception:
                self.last_alt = ""
            destination = INPUT / shop["id"] / f"{date.today().isoformat()}_online.jpg"
            media_url = target.evaluate("element => element.currentSrc || element.src || ''")
            if media_url:
                response = self.context.request.get(media_url, timeout=30000)
                content_type = response.headers.get("content-type", "").split(";")[0].lower()
                if response.ok and content_type in {"image/jpeg", "image/png", "image/webp"}:
                    destination.write_bytes(response.body())
                else:
                    target.screenshot(path=str(destination), type="jpeg", quality=92)
            else:
                target.screenshot(path=str(destination), type="jpeg", quality=92)
            return destination
        except Exception:
            debug = ERRORS / f"{shop['id']}_{datetime.now():%Y%m%d_%H%M%S}.png"
            try:
                page.screenshot(path=str(debug), full_page=False)
            except Exception:
                pass
            raise
        finally:
            page.close()

    def first_post(self, shop: dict[str, Any], url: str) -> tuple[str, Path | None]:
        """Testo e foto dell'ultimo post (Facebook) o dell'ultimo post (Instagram)."""
        self.start()
        page = self.context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            dismiss_dialogs(page)
            if "instagram.com" in url:
                post, text_css, image_css = "main", "", "a[href*='/p/'] img"
            else:
                post = "[aria-posinset='1']"
                text_css, image_css = f"{post} [data-ad-preview='message']", f"{post} a[href*='/photo'] img"
            for _ in range(12):  # Facebook carica i post solo scorrendo
                if page.locator(post).count():
                    break
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(1500)
            text = ""
            if text_css and page.locator(text_css).count():
                expand_more(page, text_css)
                text = page.locator(text_css).first.inner_text(timeout=3000)
            elif page.locator(post).count():
                text = page.locator(post).first.inner_text(timeout=3000)[:1500]  # post solo testo
            image = None
            if page.locator(image_css).count():
                target = page.locator(image_css).first
                text += "\n" + (target.get_attribute("alt", timeout=2000) or "")
                media_url = target.evaluate("element => element.currentSrc || element.src || ''")
                if media_url:
                    response = self.context.request.get(media_url, timeout=30000)
                    if response.ok:
                        image = INPUT / shop["id"] / f"{date.today().isoformat()}_avviso_controllo.jpg"
                        image.write_bytes(response.body())
            return text, image
        finally:
            page.close()

    def capture_fb_story(self, shop: dict[str, Any], source: dict[str, Any]) -> Path | None:
        """Storia Facebook (es. Le delizie di Michela): apre il profilo, clicca la storia attiva,
        scarica la foto grande al centro del visualizzatore. Usa il profilo Chrome già collegato
        a Facebook (quello di Stato.py, %LOCALAPPDATA%\\StatoFacebook\\chrome).
        Restituisce None se in questo momento non c'è nessuna storia recente (non è un errore)."""
        self.start(source)
        page = self.context.new_page()
        try:
            page.goto(source["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(int(source.get("attesa_secondi", 5)) * 1000)
            dismiss_dialogs(page)
            if "/login" in page.url or page.locator("input[type='password']").filter(visible=True).count():
                raise RuntimeError("Facebook chiede l'accesso: il profilo Chrome non è collegato")
            if "/stories/" not in page.url:
                labelled = page.get_by_role("link", name=re.compile(r"visualizza storia|view story", re.I))
                generic = page.locator("a[href*='/stories/']:not([href*='/stories/create'])")
                story = labelled.or_(generic).filter(visible=True).first
                try:
                    story.wait_for(state="visible", timeout=20000)
                    story.click()
                    page.wait_for_url(re.compile(r"facebook\.com/stories/"), timeout=15000)
                except Exception:
                    print(f"  {shop['nome']} - nessuna storia attiva sul profilo in questo momento")
                    return None
            if page.get_by_text(re.compile(r"non è più disponibile|is no longer available", re.I)).count():
                print(f"  {shop['nome']} - storia non più disponibile")
                return None
            open_story = page.get_by_text(re.compile(r"^(Clicca per visualizzare la storia|Click to view story)$", re.I)).first
            pause = page.get_by_role("button", name=re.compile(r"^(Metti in pausa|Pause)$")).first
            bucket = re.sub(r"(facebook\.com/stories/[^/?#]+).*", r"\1", page.url)
            destination = INPUT / shop["id"] / f"{date.today().isoformat()}_storia.jpg"
            # la storia può avere più parti (foto, video...): si prende la prima FOTO di oggi
            # a piena risoluzione; video e anteprime sfocate vengono saltati
            for part in range(int(source.get("parti_max", 8))):
                if part:
                    page.keyboard.press("ArrowRight")
                    page.wait_for_timeout(1500)
                    if not page.url.startswith(bucket):
                        break  # finite le parti della storia di questo profilo
                candidate = None
                for _ in range(80):  # circa 20 secondi per far arrivare la foto vera
                    try:
                        if open_story.is_visible():
                            open_story.click(timeout=2000)
                        if pause.is_visible():
                            pause.click(timeout=1000)
                    except Exception:
                        pass
                    found = page.evaluate(STORY_IMAGE_JS)
                    if found:
                        candidate = found
                        if min(found.get("w", 0), found.get("h", 0)) >= STORY_MIN_SIDE:
                            break
                    page.wait_for_timeout(250)
                if not candidate or min(candidate.get("w", 0), candidate.get("h", 0)) < STORY_MIN_SIDE:
                    print(f"  {shop['nome']} - parte {part + 1} della storia: nessuna foto nitida (video o anteprima), passo alla successiva")
                    continue
                recent = any(page.evaluate(STORY_RECENT_JS) or page.wait_for_timeout(300) for _ in range(15))
                if not recent:
                    print(f"  {shop['nome']} - parte {part + 1} della storia non risulta di oggi: non la uso")
                    continue
                response = self.context.request.get(candidate["src"], timeout=30000)
                body = response.body() if response.ok else b""
                if not image_is_sharp(body):
                    print(f"  {shop['nome']} - parte {part + 1}: Facebook ha dato solo un'immagine piccola, passo alla successiva")
                    continue
                destination.write_bytes(body)
                self.last_text = ""
                self.last_alt = candidate.get("alt", "")
                return destination
            # nessuna foto buona adesso: se stamattina ne era già stata salvata una buona, resta quella
            if destination.exists() and image_is_sharp(destination.read_bytes()):
                print(f"  {shop['nome']} - uso la foto della storia già salvata oggi")
                return destination
            if destination.exists():
                destination.unlink()  # anteprima sfocata salvata da una versione precedente
            print(f"  {shop['nome']} - nessuna foto nitida di oggi nella storia")
            return None
        except Exception:
            debug = ERRORS / f"{shop['id']}_{datetime.now():%Y%m%d_%H%M%S}.png"
            try:
                page.screenshot(path=str(debug))
            except Exception:
                pass
            raise
        finally:
            page.close()

    @staticmethod
    def _largest_media(page: Any) -> Any:
        best = None
        best_score = 0.0
        for css in ("img", "video"):
            items = page.locator(css)
            for index in range(min(items.count(), 100)):
                item = items.nth(index)
                try:
                    box = item.bounding_box()
                    if not box or box["width"] < 280 or box["height"] < 280:
                        continue
                    score = box["width"] * box["height"]
                    if score > best_score:
                        best, best_score = item, score
                except Exception:
                    continue
        return best


def image_is_sharp(data: bytes) -> bool:
    """True se i byte sono un'immagine vera con il lato minore di almeno STORY_MIN_SIDE pixel
    (scarta le anteprime sfocate e piccole che Facebook mostra mentre carica)."""
    if len(data) < 5000:
        return False
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as picture:
            return min(picture.size) >= STORY_MIN_SIDE
    except Exception:
        return False


def download_image(url: str, destination: Path) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Menu/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            raise RuntimeError(f"la risposta non è un'immagine ({content_type})")
        destination.write_bytes(response.read())
    return destination


def menu_page_date(day_text: str) -> date:
    """Converte '4 Ottobre' in una data, scegliendo l'anno più vicino a oggi."""
    match = re.search(r"(\d{1,2})\s+([a-zà]+)", day_text.lower())
    if not match or match.group(2) not in MONTHS:
        raise RuntimeError(f"data non riconosciuta: {day_text!r}")
    today = date.today()
    found = date(today.year, MONTHS[match.group(2)], int(match.group(1)))
    if (found - today).days > 180:
        found = found.replace(year=today.year - 1)
    elif (today - found).days > 180:
        found = found.replace(year=today.year + 1)
    return found


def clean_text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_paneeco(page: str, keywords: list[str]) -> tuple[date, list[dict[str, Any]]]:
    """Estrae data e sezioni 'del giorno' dalla pagina menu di paneeco.it."""
    header = re.search(r'class="menu-header__title"[^>]*>(.*?)</h1>', page, re.S)
    if not header:
        raise RuntimeError("data del menu non trovata nella pagina")
    menu_day = menu_page_date(clean_text(header.group(1)))
    sections = []
    for block in page.split('<section class="menu-category')[1:]:
        title_match = re.search(r'class="menu-category__title"[^>]*>(.*?)</h2>', block, re.S)
        title = clean_text(title_match.group(1)) if title_match else ""
        if not title or not any(key.lower() in title.lower() for key in keywords):
            continue
        items = []
        for article in re.findall(r'<article class="menu-item".*?</article>', block, re.S):
            def field(css: str) -> str:
                found = re.search(rf'class="menu-item__{css}"[^>]*>(.*?)</(?:p|span)>', article, re.S)
                return clean_text(found.group(1)) if found else ""
            if field("name"):
                items.append({"nome": field("name"), "prezzo": field("price"), "descrizione": field("description")})
        if items:
            sections.append({"titolo": title, "piatti": items})
    if not sections:
        raise RuntimeError("nessuna sezione 'del giorno' trovata")
    return menu_day, sections


def download_menu_page(shop: dict[str, Any], source: dict[str, Any]) -> tuple[Path, date]:
    request = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0 Menu/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        page = response.read().decode("utf-8", errors="replace")
    menu_day, sections = parse_paneeco(page, source.get("sezioni", ["del giorno"]))
    destination = INPUT / shop["id"] / f"{menu_day.isoformat()}_sito.json"
    save_json(destination, {"data": menu_day.isoformat(), "fonte": source["url"], "sezioni": sections})
    return destination, menu_day


def date_in_text(text: str) -> date | None:
    """Cerca nel testo una data tipo '4 Ottobre' o '04/10'."""
    if not text:
        return None
    for number, month in re.findall(r"(\d{1,2})\s+([a-zà]+)", text.lower()):
        if month in MONTHS:
            try:
                return menu_page_date(f"{number} {month}")
            except (RuntimeError, ValueError):
                pass
    match = re.search(r"\b(\d{1,2})[/.-](\d{1,2})\b", text)
    if match:
        try:
            return menu_page_date(f"{int(match.group(1))} {list(MONTHS)[int(match.group(2)) - 1]}")
        except (IndexError, RuntimeError, ValueError):
            pass
    return None


def dates_in_text(text: str) -> list[date]:
    """Tutte le date nel testo, nell'ordine: '4 ottobre', '4/10', '4.10' ..."""
    found: list[tuple[int, date]] = []
    lowered = (text or "").lower()
    for match in re.finditer(r"(\d{1,2})\s+([a-zà]+)", lowered):
        if match.group(2) in MONTHS:
            try:
                found.append((match.start(), menu_page_date(f"{match.group(1)} {match.group(2)}")))
            except (RuntimeError, ValueError):
                pass
    for match in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})\b", lowered):
        try:
            month = list(MONTHS)[int(match.group(2)) - 1]
            found.append((match.start(), menu_page_date(f"{int(match.group(1))} {month}")))
        except (IndexError, RuntimeError, ValueError):
            pass
    return [day for _, day in sorted(found)]


WEEKDAYS = {"lun": 0, "mar": 1, "mer": 2, "gio": 3, "ven": 4, "sab": 5, "dom": 6}


def fix_ocr_dates(text: str) -> str:
    """Corregge gli errori tipici dell'OCR sulle date: "8/ IO" -> "8/10", "7110" -> "7/10"."""
    fixed = re.sub(r"(?<=[/.\-])\s*[IlO|]{1}[O0]\b|(?<=[/.\-])\s*[1Il|][O0o]\b", "10", text)
    fixed = re.sub(r"\b[IlO|][O0]\b", "10", fixed)          # "IO" isolato dopo una barra spezzata
    fixed = re.sub(r"(\d{1,2})\s*/\s*(\d{1,2})", r"\1/\2", fixed)  # "8/ 10" -> "8/10"
    # "7110" = "7/10" (la barra letta come 1): giorno 1-31, mese 1-12
    fixed = re.sub(r"\b([1-9]|[12]\d|3[01])[1Il|](1[0-2]|0?[1-9])\b", r"\1/\2", fixed)
    return fixed


def closure_period(text: str) -> tuple[date, date] | None:
    """Riconosce un avviso di chiusura ("Siamo chiusi da domenica 4/10 a mercoledì 7/10").

    Restituisce (primo giorno, ultimo giorno di chiusura) oppure None. Regole, in ordine:
    1. "da <giorno> [data] a <giorno> [data]": usa le date se lette, altrimenti i nomi dei
       giorni (es. "da domenica a mercoledì 7/10" -> domenica 4/10 - mercoledì 7/10);
    2. due date qualsiasi nel testo;
    3. una sola data con "fino" -> da oggi fino a quella data.
    Il testo letto dall'OCR viene prima corretto (vedi fix_ocr_dates)."""
    # cerco le date solo vicino alla parola chiave: così "aperti da lunedì a sabato,
    # domenica chiusi" o un "chiuso il lunedì" in fondo a una pagina non diventano chiusure
    for keyword in re.finditer(r"\bchius[oiae]\b|\bchiusura\b|\bferie\b", text or "", re.IGNORECASE):
        segment = text[keyword.start():keyword.start() + 160]
        segment = re.split(r"[.!?;](?:\s|$)", segment)[0]  # solo la frase dell'avviso
        found = closure_in_segment(segment)
        if found:
            return found
    return None


def closure_in_segment(text: str) -> tuple[date, date] | None:
    """Regole di closure_period applicate al pezzo di testo che segue la parola chiave."""
    text = fix_ocr_dates(text)
    lowered = text.lower()
    day_word = r"(lun|mar|mer|gio|ven|sab|dom)[a-zàèéìòù]*"
    date_part = r"\s*(\d{1,2}/\d{1,2}|\d{1,2}\s+[a-z]+)?"
    match = re.search(rf"\bdal?\s+{day_word}{date_part}\s+(?:a|al|fino\s+a[l]?)\s+{day_word}{date_part}", lowered)
    if match:
        first_name, first_text, last_name, last_text = match.groups()
        first_dates = dates_in_text(first_text or "")
        last_dates = dates_in_text(last_text or "")
        today = date.today()
        if last_dates:
            last = last_dates[0]
        else:  # prossimo <giorno> a partire da oggi
            last = today + timedelta(days=(WEEKDAYS[last_name] - today.weekday()) % 7)
        if first_dates:
            first = first_dates[0]
        else:  # ultimo <giorno> non successivo alla fine della chiusura
            first = last - timedelta(days=(last.weekday() - WEEKDAYS[first_name]) % 7)
        if first <= last:
            return first, last
    days = dates_in_text(text)
    if len(days) >= 2:
        return min(days[0], days[1]), max(days[0], days[1])
    if len(days) == 1 and re.search(r"\bfino\b", text, re.IGNORECASE):
        return min(date.today(), days[0]), days[0]
    if not days and re.search(r"\boggi\b", text, re.IGNORECASE):  # "oggi siamo chiusi"
        return date.today(), date.today()
    return None


def closure_today(text: str) -> tuple[date, date] | None:
    """Periodo di chiusura solo se comprende oggi."""
    closed = closure_period(text)
    return closed if closed and closed[0] <= date.today() <= closed[1] else None


def save_closure(folder: Path, closed: tuple[date, date], image: Path | None = None, text: str = "") -> Path:
    """Salva l'avviso come menu di oggi: AAAA-MM-GG_chiusura_fino_AAAAMMGG.jpg (la foto)
    oppure .json (avviso solo testuale, mostrato come testo nella scheda)."""
    stem = f"{date.today().isoformat()}_chiusura_fino_{closed[1]:%Y%m%d}"
    if image:
        dated = folder / f"{stem}{image.suffix}"
        if dated != image:
            shutil.copy2(image, dated)
        return dated
    dated = folder / f"{stem}{TEXT_EXTENSION}"
    save_json(dated, {"data": date.today().isoformat(), "fonte": "avviso", "sezioni": [
        {"titolo": "Avviso di chiusura", "piatti": [{"nome": " ".join(text.split())[:600], "prezzo": "", "descrizione": ""}]}]})
    return dated


NOTICE_STATE = DATA / "avvisi.json"
NOTICE_MINUTES = 60  # ogni quanto rileggere i post per cercare avvisi (impostazioni > avvisi_ogni_minuti)


def page_text(url: str) -> str:
    """Testo visibile di una pagina web (senza tag), per cercare avvisi sui siti."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Menu/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read().decode("utf-8", errors="replace")
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    return html.unescape(re.sub(r"<[^>]+>", " ", raw))


def check_notice(shop: dict[str, Any], browser: "BrowserCollector | None", checked: str) -> Result | None:
    """Cerca un avviso di chiusura nell'ultimo post (Facebook/Instagram) o nel sito del locale,
    sia nel testo sia dentro la foto (OCR). Al massimo una volta ogni NOTICE_MINUTES per locale."""
    url = shop.get("url", "")
    if not url:
        return None
    state = read_json(NOTICE_STATE, {})
    last = state.get(shop["id"], "")
    if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() < NOTICE_MINUTES * 60:
        return None
    state[shop["id"]] = datetime.now().isoformat(timespec="seconds")
    save_json(NOTICE_STATE, state)
    folder = INPUT / shop["id"]
    image: Path | None = None
    try:
        if "facebook.com" in url or "instagram.com" in url:
            if browser is None:
                return None
            text, image = browser.first_post(shop, url)
        else:
            text = page_text(url)
    except Exception as exc:
        print(f"  {shop['nome']} - controllo avvisi non riuscito: {str(exc)[:120]}")
        return None
    photo_text = ocr_image(image) if image else ""
    learn_rest_days(shop, f"{text}\n{photo_text}")
    closed = closure_today(f"{text}\n{photo_text}")
    if not closed:
        if image:
            image.unlink(missing_ok=True)  # foto usata solo per cercare l'avviso
        return None
    print(f"  {shop['nome']} - avviso di chiusura dal {closed[0]:%d/%m} al {closed[1]:%d/%m}")
    from_photo = image is not None and closure_today(photo_text) is not None
    saved = save_closure(folder, closed, image if from_photo else None, text)
    if image:
        image.unlink(missing_ok=True)
    return Result(saved, date.today(), "avviso", checked)


OCR_SCRIPT = ROOT / "ocr.ps1"
OCR_CACHE = DATA / "ocr.json"


def ocr_image(path: Path) -> str:
    """Testo scritto dentro la foto, letto con l'OCR integrato di Windows (ocr.ps1).

    I risultati sono ricordati in dati/ocr.json per impronta del file: la stessa foto
    non viene riletta a ogni giro. Su sistemi diversi da Windows restituisce ""."""
    if sys.platform != "win32" or not OCR_SCRIPT.exists() or not path.exists():
        return ""
    import subprocess
    key = "v2:" + file_hash(path)  # v2: foto ingrandita prima dell'OCR
    cache = read_json(OCR_CACHE, {})
    if key in cache:
        return cache[key]
    source = path
    try:  # l'OCR di Windows legge meglio le scritte grandi: ingrandisco la foto (se c'è Pillow)
        from PIL import Image
        with Image.open(path) as picture:
            factor = max(1, min(3, 2000 // max(picture.size)))
            if factor > 1:
                source = DATA / "ocr_temp.png"
                picture.convert("RGB").resize((picture.width * factor, picture.height * factor),
                                              Image.LANCZOS).save(source)
    except Exception:
        source = path
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT),
                               str(source)], capture_output=True, timeout=90, encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        print(f"  OCR non riuscito: {exc}")
        return ""
    if done.returncode != 0:
        print(f"  OCR non riuscito: {(done.stderr or '').strip()[:200]}")
        return ""
    text = " ".join(done.stdout.split())
    cache[key] = text
    save_json(OCR_CACHE, dict(list(cache.items())[-200:]))  # tiene solo le ultime 200 foto
    return text


DAY_NAMES = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
REST_STATE = DATA / "riposi.json"


def rest_days_in_text(text: str) -> set[int]:
    """Giorni di riposo settimanale scritti in un post o in una foto degli orari, es.
    "Lunedì GIORNO DI CHIUSURA", "chiuso il lunedì", "riposo settimanale: domenica".
    Restituisce i numeri dei giorni (0 = lunedì). Un "lunedì chiuso" isolato conta solo
    se il testo è una tabella di orari (almeno tre orari tipo 9:30 - 20:00)."""
    lowered = (text or "").lower()
    day = r"(lun|mar|mer|gio|ven|sab|dom)[a-zàèéìòù]*"
    found: set[int] = set()
    patterns = [rf"\b{day}\W+giorno di chiusura", rf"\b{day}\W+(?:riposo|chiusura) settimanale",
                rf"(?:riposo|chiusura) settimanale\W+(?:il\s+)?{day}", rf"\bchius[oai]\s+(?:il|ogni)\s+{day}"]
    if len(re.findall(r"\d{1,2}[:.]\d{2}\s*-\s*\d{1,2}[:.]\d{2}", lowered)) >= 3:
        patterns.append(rf"\b{day}\W{{0,3}}chius[oaiu]")
    for pattern in patterns:
        for match in re.finditer(pattern, lowered):
            found.add(WEEKDAYS[match.group(1)])
    return found


def learn_rest_days(shop: dict[str, Any], text: str) -> None:
    """Ricorda (dati/riposi.json) i giorni di riposo trovati nei post del locale."""
    days = rest_days_in_text(text)
    if not days:
        return
    state = read_json(REST_STATE, {})
    known = set(state.get(shop["id"], []))
    if not days <= known:
        state[shop["id"]] = sorted(known | days)
        save_json(REST_STATE, state)
        print(f"  {shop['nome']} - giorno di riposo settimanale: {', '.join(DAY_NAMES[d] for d in sorted(days))}")


def rest_days(shop: dict[str, Any]) -> set[int]:
    """Giorni di riposo: quelli scritti in locali.json ("riposo": ["lunedì"]) più quelli letti nei post."""
    configured = {WEEKDAYS[name.strip().lower()[:3]] for name in shop.get("riposo", []) if name.strip()[:3].lower() in WEEKDAYS}
    return configured | set(read_json(REST_STATE, {}).get(shop["id"], []))


def rest_day_result(shop: dict[str, Any], folder: Path, checked: str) -> Result:
    """Oggi è il giorno di riposo: la scheda mostra l'avviso al posto del menu."""
    today = date.today()
    path = folder / f"{today.isoformat()}_riposo.json"
    if not path.exists():
        text = f"Oggi, {DAY_NAMES[today.weekday()]}, {shop['nome']} è chiuso per il riposo settimanale."
        save_json(path, {"data": today.isoformat(), "fonte": "riposo", "sezioni": [
            {"titolo": "Giorno di riposo", "piatti": [{"nome": text, "prezzo": "", "descrizione": ""}]}]})
    return Result(path, today, "riposo settimanale", checked)


def closure_note(path: Path | None) -> str:
    """"Chiuso fino al 07/10" se il file è un avviso di chiusura salvato da closure_period."""
    if path and path.stem.endswith("_riposo"):
        return f"Chiuso il {DAY_NAMES[image_date(path).weekday()]} (riposo settimanale)"
    match = re.search(r"_chiusura_fino_(\d{4})(\d{2})(\d{2})", path.stem) if path else None
    return f"Chiuso fino al {match.group(3)}/{match.group(2)}" if match else ""


def text_post_result(shop: dict[str, Any], source: dict[str, Any], folder: Path, text: str, checked: str) -> Result:
    """Post senza foto: avviso di chiusura oppure menu scritto nel testo del post.
    Il menu viene salvato come AAAA-MM-GG_testo.json e mostrato riga per riga nella scheda."""
    name = source.get("nome", "browser")
    print(f"  {shop['nome']} - testo letto (post senza foto): {' '.join(text.split())[:160]}")
    learn_rest_days(shop, text)
    closed = closure_today(text)
    if closed:
        print(f"  {shop['nome']} - avviso di chiusura dal {closed[0]:%d/%m} al {closed[1]:%d/%m}")
        return Result(save_closure(folder, closed, None, text), date.today(), name, checked)
    words = source.get("parole_menu", MENU_WORDS)
    if words and not any(word in text.lower() for word in words):
        raise RuntimeError("l'ultimo post (solo testo) non sembra un menu")
    clean = "\n".join(line.strip() for line in clean_post_text(text).splitlines() if line.strip())
    written_day = plausible_menu_date(clean)  # data o giorno scritto nel post ("MENÙ DI SABATO")
    for earlier in sorted(folder.glob("*_testo.json")):  # stesso testo già visto: resta la sua data
        if read_json(earlier, {}).get("testo") == clean:
            if written_day and written_day != image_date(earlier):
                corrected = folder / f"{written_day.isoformat()}_testo.json"
                earlier.replace(corrected)
                return Result(corrected, written_day, name, checked)
            return Result(earlier, image_date(earlier), name, checked)
    day = written_day or date.today()
    lines = [line for line in clean.splitlines() if not line.lower().startswith(("altro", "mostra"))]
    path = folder / f"{day.isoformat()}_testo.json"
    save_json(path, {"data": day.isoformat(), "testo": clean, "sezioni": [
        {"titolo": "Menu del giorno", "piatti": [{"nome": line, "prezzo": "", "descrizione": ""} for line in lines]}]})
    return Result(path, day, name, checked)


MENU_WORDS = ["menu", "menù", "primi", "secondi", "contorni", "del giorno", "piatti", "antipasti"]


def plausible_menu_date(text: str) -> date | None:
    """Data del menu scritta nel post o nella foto (es. "Menu del giorno Sabato 3/10"),
    accettata solo se è tra 7 giorni fa e domani (evita prezzi o date lontane)."""
    if not text:
        return None
    for day in dates_in_text(fix_ocr_dates(text)):
        if -7 <= (day - date.today()).days <= 1:
            return day
    # nessuna data ma il giorno della settimana: "MENÙ DI SABATO", "Menu del giorno lunedì"
    match = re.search(r"\bmen[uù]\b[^\n]{0,25}?\b(lun|mar|mer|gio|ven|sab|dom)[a-zàèéìòù]*", text.lower())
    if match:
        today = date.today()
        return today - timedelta(days=(today.weekday() - WEEKDAYS[match.group(1)]) % 7)
    return None


def keep_first_seen(folder: Path, captured: Path) -> tuple[Path, date]:
    """Se l'immagine catturata è identica a una già presente, non è un menu nuovo:
    elimina la copia appena scaricata e mantiene la data della prima volta in cui è comparsa."""
    digest = file_hash(captured)
    earlier = [item for item in folder.glob("*") if item.is_file() and item != captured
               and item.suffix.lower() in IMAGE_EXTENSIONS and file_hash(item) == digest]
    if earlier:
        first = min(earlier, key=lambda item: (image_date(item), item.stat().st_mtime))
        captured.unlink()
        return first, image_date(first)
    return captured, date.today()


def acquire(shop: dict[str, Any], browser: BrowserCollector | None, online: bool,
            skip_if_today: str | None = None) -> Result:
    """Cerca il menu del locale provando le fonti nell'ordine.

    skip_if_today: se non è None e in ingresso c'è già un menu di oggi, non va online
    (modalità automatica: evita accessi inutili a Facebook/Instagram); il valore è
    il nome della fonte da mostrare."""
    checked = datetime.now().astimezone().isoformat(timespec="seconds")
    errors: list[str] = []
    folder = INPUT / shop["id"]
    folder.mkdir(parents=True, exist_ok=True)
    if date.today().weekday() in rest_days(shop):
        return rest_day_result(shop, folder, checked)
    if online and skip_if_today is not None:
        latest = newest_image(folder)
        if latest and latest.stem.endswith("_storia") and not image_is_sharp(latest.read_bytes()):
            latest.unlink()  # anteprima sfocata di una storia: va ricatturata
            latest = newest_image(folder)
        # un menu solo testo viene comunque riletto: il post può essere stato completato o corretto
        if latest and image_date(latest) == date.today() and not latest.stem.endswith("_testo"):
            return Result(latest, date.today(), skip_if_today, checked)
    if online:
        notice = check_notice(shop, browser, checked)
        if notice:
            return notice
    result = acquire_sources(shop, browser, online, folder, checked)
    # anche una foto messa a mano in ingresso o scaricata può essere un avviso di chiusura
    image = result.image
    if image and image.suffix.lower() in IMAGE_EXTENSIONS and "_chiusura_fino_" not in image.name:
        closed = closure_today(ocr_image(image))
        if closed:
            print(f"  {shop['nome']} - avviso di chiusura (foto) dal {closed[0]:%d/%m} al {closed[1]:%d/%m}")
            return Result(save_closure(folder, closed, image), date.today(), result.source, checked)
    return result


def acquire_sources(shop: dict[str, Any], browser: BrowserCollector | None, online: bool,
                    folder: Path, checked: str) -> Result:
    """Prova le fonti del locale nell'ordine indicato in locali.json."""
    errors: list[str] = []
    for source in shop.get("fonti", [{"tipo": "cartella"}]):
        if not source.get("attiva", True):
            continue
        kind = source.get("tipo", "cartella")
        try:
            if kind == "cartella":
                image = newest_image(folder)
                if image:
                    return Result(image, image_date(image), "cartella", checked, "; ".join(errors))
                raise RuntimeError("nessuna immagine")
            if not online:
                continue
            destination = folder / f"{date.today().isoformat()}_online.jpg"
            if kind == "immagine":
                image = download_image(source["url"], destination)
            elif kind == "pagina":
                image, page_day = download_menu_page(shop, source)
                return Result(image, page_day, source.get("nome", kind), checked)
            elif kind == "storia_facebook":
                if browser is None:
                    raise RuntimeError("browser non disponibile")
                image = browser.capture_fb_story(shop, source)
                if image is None:
                    raise RuntimeError("nessuna storia di oggi in questo momento")
                image, new_day = keep_first_seen(folder, image)  # stessa foto di ieri = non è il menu di oggi
                return Result(image, new_day, source.get("nome", kind), checked)
            elif kind == "browser":
                if browser is None:
                    raise RuntimeError("browser non disponibile")
                image = browser.capture(shop, source)
                if image is None:  # post solo testo
                    return text_post_result(shop, source, folder, browser.last_text, checked)
                photo_text = ocr_image(image)  # testo scritto nella foto (es. avviso di chiusura)
                seen = " ".join(f"{browser.last_text} {photo_text or browser.last_alt}".split())
                if seen:
                    print(f"  {shop['nome']} - testo letto: {seen[:160]}")  # utile nel registro
                learn_rest_days(shop, f"{browser.last_text}\n{photo_text}")
                closed = closure_period(f"{browser.last_text}\n{browser.last_alt}\n{photo_text}")
                if closed:
                    print(f"  {shop['nome']} - avviso di chiusura dal {closed[0]:%d/%m} al {closed[1]:%d/%m}")
                if closed and closed[0] <= date.today() <= closed[1]:
                    # avviso di chiusura valido oggi: lo pubblico come "menu del giorno"
                    image, _ = keep_first_seen(folder, image)
                    if image.suffix.lower() in IMAGE_EXTENSIONS and closure_today(photo_text):
                        return Result(save_closure(folder, closed, image), date.today(), source.get("nome", kind), checked)
                    return Result(save_closure(folder, closed, None, browser.last_text), date.today(),
                                  source.get("nome", kind), checked)
                # l'ultimo post è davvero un menu? (non una pubblicità, una foto di un piatto, ecc.)
                words = source.get("parole_menu", MENU_WORDS)
                if words and not any(word in f"{browser.last_text} {photo_text}".lower() for word in words):
                    image.unlink(missing_ok=True)
                    raise RuntimeError("l'ultimo post non sembra un menu")
                text_day = plausible_menu_date(browser.last_text) or plausible_menu_date(photo_text)
                if text_day:
                    # data scritta nel post (es. "menù del giorno 4 Ottobre"): rinomino il file con quella data
                    dated = folder / f"{text_day.isoformat()}_online{image.suffix}"
                    if dated != image:
                        image.replace(dated)
                    return Result(dated, text_day, source.get("nome", kind), checked)
                if source.get("data") == "novita":
                    image, new_day = keep_first_seen(folder, image)
                    return Result(image, new_day, source.get("nome", kind), checked)
            else:
                raise RuntimeError(f"tipo fonte sconosciuto: {kind}")
            return Result(image, date.today(), source.get("nome", kind), checked)
        except Exception as exc:
            errors.append(f"{kind}: {exc}")
    old = newest_image(folder)
    return Result(old, image_date(old) if old else None, "ultimo disponibile" if old else "nessuna", checked, "; ".join(errors))


def status_label(menu_day: date | None, error: str) -> tuple[str, str]:
    if menu_day == date.today():
        return ("Oggi", "fresh")
    if menu_day:
        return ("Non di oggi", "stale")
    return (("Errore" if error else "Non disponibile"), "missing")


def write_manifest() -> str:
    """Scrive manifest.webmanifest con le icone "versionate" e restituisce la versione.

    La versione (?v=...) dipende dal contenuto dell'icona: quando il logo cambia
    l'indirizzo cambia e iPhone/Android non possono usare l'icona vecchia in cache."""
    icon = ICONS / "icona-512.png"
    iv = file_hash(icon)[:10] if icon.exists() else "0"
    manifest = {
        "name": "Menu", "short_name": "Menu", "description": "Menu del giorno delle rosticcerie",
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#0b1220", "theme_color": "#0b1220", "lang": "it",
        "icons": [
            {"src": f"icone/icona-192.png?v={iv}", "sizes": "192x192", "type": "image/png"},
            {"src": f"icone/icona-512.png?v={iv}", "sizes": "512x512", "type": "image/png"},
            {"src": f"icone/icona-512.png?v={iv}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    (ROOT / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # versione amministratore: aggiunta alla Home da una pagina aperta con ?v=57, l'app si apre con ?v=57
    admin = {**manifest, "start_url": "./?v=57", "id": "./?v=57"}
    (ROOT / "manifest-admin.webmanifest").write_text(json.dumps(admin, ensure_ascii=False, indent=2), encoding="utf-8")
    return iv


def sort_key_name(name: str) -> str:
    """Chiave per l'ordine alfabetico italiano: senza accenti e senza maiuscole."""
    return "".join(ch for ch in unicodedata.normalize("NFD", name) if not unicodedata.combining(ch)).casefold()


def display_order(shops: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[int]:
    """Ordine delle schede: prima le rosticcerie "Oggi" (fascia verde: menu, avviso di chiusura o
    riposo pubblicato oggi), poi le altre; in ciascun gruppo in ordine alfabetico. La pagina rifà
    lo stesso ordinamento quando cambia il giorno."""
    today = date.today().isoformat()
    def key(i: int) -> tuple[bool, str]:
        published = results[i].get("menu_date") == today
        return (not published, sort_key_name(shops[i].get("nome", "")))
    return sorted(range(len(shops)), key=key)


def generate_html(settings: dict[str, Any], shops: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    order = display_order(shops, results)
    shops, results = [shops[i] for i in order], [results[i] for i in order]
    cards = []
    logos = make_shop_logos(shops)
    for index, (shop, result) in enumerate(zip(shops, results)):
        label, css = status_label(date.fromisoformat(result["menu_date"]) if result.get("menu_date") else None, result.get("error", ""))
        menu_day = date.fromisoformat(result["menu_date"]) if result.get("menu_date") else None
        day_text = menu_day.strftime("%d/%m/%Y") if menu_day else "nessun menu"
        note_html = f'\n  <span class="note">{html.escape(result["nota"])}</span>' if result.get("nota") else ""
        logo = logos.get(shop["id"])
        logo_html = f'<img class="logo" src="{html.escape(logo)}" alt="" width="64" height="64">\n  ' if logo else ""
        cards.append(f'''<button type="button" class="card {'band-ok' if css == 'fresh' else 'band-old'}" data-index="{index}">
  {logo_html}<span class="info">
  <h2>{html.escape(shop['nome'])}</h2>
  <span class="day"><strong>{day_text}</strong></span>{note_html}
  <span class="status {css}">{label}</span>
  </span>
  <span class="count" hidden></span>
</button>''')
    public_shops = [{**{key: shop.get(key, "") for key in ("nome", "telefono", "indirizzo", "url")},
                     # nomi usati prima di un cambio di nome: i loro click restano sommati al locale
                     "nomi_precedenti": shop.get("nomi_precedenti", [])} for shop in shops]
    # niente orari di controllo nella pagina: se i menu non cambiano la pagina resta identica
    # e la pubblicazione automatica non crea un nuovo invio ogni 15 minuti
    public_results = [{key: value for key, value in result.items() if key != "checked_at"} for result in results]
    version = hashlib.sha1(json.dumps(public_results, sort_keys=True).encode()).hexdigest()[:10]
    title_tpl = settings.get("titolo", "Menu - {data}")
    payload = json.dumps({"shops": public_shops, "results": public_results, "v": version, "titolo": title_tpl,
                          "log": settings.get("registro_click_url", "")}, ensure_ascii=False).replace("</", "<\\/")
    days = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    today = date.today()
    today_text = f"{days[today.weekday()]} {today.day} {list(MONTHS)[today.month - 1]}"
    title = html.escape(settings.get("titolo", "Menu - {data}").replace("{data}", today_text))
    iv = write_manifest()  # versione delle icone: cambia quando cambia il logo
    document = f'''<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1220"><title>Menu</title>
<meta property="og:title" content="Menu"><meta property="og:description" content="Menu del giorno delle rosticcerie">
<meta property="og:type" content="website"><meta property="og:url" content="{WEB_URL}">
<meta property="og:image" content="{WEB_URL}icone/icona-512.png?v={iv}"><meta property="og:image:width" content="512"><meta property="og:image:height" content="512">
<script>/* con ?v=57 il manifest "amministratore": "+Home" crea l'app che si apre già con ?v=57 */
document.write('<link rel="manifest" href="'+(new URLSearchParams(location.search).get('v')==='57'?'manifest-admin':'manifest')+'.webmanifest?v={iv}">')</script><link rel="icon" type="image/png" href="icone/favicon.png?v={iv}">
<link rel="apple-touch-icon" sizes="180x180" href="icone/icona-180.png?v={iv}"><meta name="apple-mobile-web-app-title" content="Menu">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
:root{{--bg:#0b1220;--card:#fff;--ink:#172033;--muted:#64748b;--accent:#16a34a}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(150deg,#09111f,#172033);font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:white;min-height:100vh}}
header{{max-width:1500px;margin:auto;padding:max(28px,calc(env(safe-area-inset-top) + 12px)) 20px 18px;display:flex;justify-content:space-between;align-items:end;gap:20px}}h1{{margin:0;font-size:clamp(28px,4vw,46px)}}header p{{margin:5px 0 0;color:#cbd5e1}}.updated{{font-size:13px;color:#94a3b8}}
main{{max-width:1500px;margin:auto;padding:12px 20px 40px;display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:18px}}
.card{{display:flex;flex-direction:column;align-items:flex-start;gap:8px;text-align:left;width:100%;background:var(--card);color:var(--ink);border-radius:16px;padding:18px 20px;box-shadow:0 10px 28px #0005;cursor:pointer;transition:.18s transform,.18s box-shadow;font:inherit;position:relative}}
.card{{flex-direction:row;align-items:center;gap:16px}}.info{{display:flex;flex-direction:column;align-items:flex-start;gap:8px;min-width:0;flex:1}}.logo{{width:64px;height:64px;flex:none;border-radius:14px;object-fit:cover;background:#fff;box-shadow:0 2px 8px #0003}}
.count{{position:absolute;right:14px;bottom:12px;min-width:28px;padding:2px 9px;border-radius:999px;background:#1e293b;color:#fff;font-size:14px;font-weight:800;text-align:center}}
.infocard{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;background:#d1d5db;color:#172033;border:4px solid #facc15;border-radius:16px;padding:18px 20px;box-shadow:0 10px 28px #0005;text-align:center;min-height:120px}}
.infocard h2{{margin:0;font-size:21px}}.infocard .total{{font-size:46px;font-weight:800;line-height:1.1;color:#172033}}.infocard small{{color:#475569;font-size:14px}}.infocard{{cursor:pointer}}.infocard:hover,.infocard:focus-visible{{transform:translateY(-3px);outline:3px solid #facc15}}
#stats{{width:min(620px,96vw)}}#stats .x{{background:#334155;color:white;font-size:18px}}.statwrap{{padding:10px 14px 16px;overflow:auto}}#stats table{{width:100%;border-collapse:collapse;font-size:16px}}#stats th,#stats td{{padding:8px 10px;border-bottom:1px solid #1e293b;text-align:right}}#stats th:first-child,#stats td:first-child{{text-align:left}}#stats th{{color:#facc15;font-size:14px}}#stats tfoot td{{font-weight:800;border-top:2px solid #facc15;border-bottom:0}}.statnote{{margin:10px 0 0;color:#94a3b8;font-size:13px}}.card:hover,.card:focus-visible{{transform:translateY(-3px);box-shadow:0 14px 32px #0008;outline:3px solid var(--accent)}}
.band-ok{{border-left:10px solid #16a34a}}#detail{{box-sizing:border-box}}#detail.ok{{border:5mm solid #16a34a}}#detail.old{{border:5mm solid #f97316}}.band-old{{border-left:10px solid #f97316;background:#dfe4ec}}
.card h2{{font-size:21px;margin:0}}.day{{color:var(--muted);font-size:15px}}.day strong{{color:var(--ink)}}.note{{color:#b45309;font-size:14px;font-weight:700}}.status{{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:750}}.fresh{{background:#dcfce7;color:#166534}}.stale{{background:#fef3c7;color:#92400e}}.missing{{background:#fee2e2;color:#991b1b}}
#text{{max-height:68vh;overflow:auto;padding:0 18px}}#text h3{{margin:18px 0 8px;color:#86efac}}.dish{{display:grid;grid-template-columns:1fr auto;gap:2px 12px;padding:8px 0;border-bottom:1px solid #1e293b}}.dish b{{white-space:nowrap}}.dish small{{grid-column:1/-1;color:#94a3b8}}.dish small:empty{{display:none}}
#nomenu{{padding:40px 18px;text-align:center;color:#cbd5e1}}
dialog{{width:min(920px,96vw);max-height:94vh;padding:0;border:0;border-radius:18px;background:#050a12;color:white;box-shadow:0 24px 70px #000b}}dialog::backdrop{{background:#000c}}.modal-head{{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #334155}}.nav{{position:fixed;top:50%;transform:translateY(-50%);z-index:5;width:52px;height:52px;padding:0;border-radius:50%;background:#334155e6;color:#fff;font-size:34px;line-height:48px;box-shadow:0 6px 18px #0008}}.nav:hover,.nav:focus-visible{{background:#475569}}#prev{{left:max(6px,calc(50vw - min(460px,48vw) - 66px))}}#next{{right:max(6px,calc(50vw - min(460px,48vw) - 66px))}}
.modal-head h2{{margin:0}}#addr{{margin:4px 0 0;font-size:15px;font-weight:400;color:#cbd5e1}}button{{border:0;border-radius:10px;padding:10px 14px;font-weight:700;cursor:pointer}}#close{{background:#334155;color:white;font-size:18px}}#full{{display:block;max-width:100%;max-height:68vh;margin:auto;object-fit:contain}}#full[hidden]{{display:none}}.actions{{padding:14px 18px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}}.actions a{{color:white;text-decoration:none;background:#166534;padding:10px 14px;border-radius:10px;font-weight:700}}.actions .source{{background:#1d4ed8}}#meta{{color:#cbd5e1;margin-right:auto}}footer{{text-align:center;color:#94a3b8;padding:0 20px 28px;font-size:13px}}
@media(max-width:600px){{header{{align-items:start;flex-direction:column}}main{{grid-template-columns:1fr;padding-inline:12px}}}}
</style></head><body>
<header><h1>{title}</h1></header>
<main>{''.join(cards)}</main>
<dialog id="detail"><div class="modal-head"><div><h2 id="name"></h2><p id="addr" hidden></p></div><button id="close" aria-label="Chiudi">✕</button></div><button class="nav" id="prev" aria-label="Rosticceria precedente">‹</button><button class="nav" id="next" aria-label="Rosticceria successiva">›</button><img id="full" alt=""><div id="text"></div><p id="nomenu" hidden>Il menu di questa data non è pubblicato (vedi archivio).</p><div class="actions"><span id="meta"></span><a id="phone" hidden></a><a id="map" target="_blank" rel="noopener" hidden>Google Maps</a><a id="source" class="source" target="_blank" rel="noopener" hidden>Fonte</a></div></dialog>
<script>const DATA={payload};
/* Registro dei click sul foglio Google "Menu" (Apps Script in Registro_click.gs).
   ?v=57 nell'indirizzo = amministratore: quei click non vengono registrati. Senza ?v=57 si registra
   sempre, anche sullo stesso dispositivo (non viene ricordato nulla). */
const Q=new URLSearchParams(location.search);const ADMIN=Q.get('v')==='57';
try{{localStorage.removeItem('menuAdmin')}}catch(e){{}}
function deviceLabel(){{const u=navigator.userAgent||'';let o='Altro',b='Altro';
if(/iPad/.test(u))o='iPad';else if(/iPhone/.test(u))o='iPhone';else if(/Android/.test(u))o='Android';else if(/Macintosh/.test(u))o='Mac';else if(/Windows/.test(u))o='Windows';else if(/Linux/.test(u))o='Linux';
if(/Edg\\//.test(u))b='Edge';else if(/OPR\\//.test(u))b='Opera';else if(/CriOS\\//.test(u)||/Chrome\\//.test(u))b='Chrome';else if(/FxiOS\\//.test(u)||/Firefox\\//.test(u))b='Firefox';else if(/Safari\\//.test(u))b='Safari';return o+' / '+b}}
let placeP=null;function approxPlace(){{if(!placeP)placeP=fetch('https://ipwho.is/',{{cache:'no-store'}}).then(r=>r.json()).then(d=>d&&d.success!==false?[d.city,d.region,d.country_code].filter(Boolean).join(', '):'').catch(()=>'');return placeP}}
if(!ADMIN&&DATA.log)approxPlace();
function sendLog(body){{try{{if(navigator.sendBeacon&&navigator.sendBeacon(DATA.log,new Blob([body],{{type:'text/plain'}})))return}}catch(e){{}}try{{fetch(DATA.log,{{method:'POST',mode:'no-cors',keepalive:true,body}})}}catch(e){{}}}}
function logClick(name){{if(ADMIN||!DATA.log)return;Promise.race([approxPlace(),new Promise(r=>setTimeout(()=>r(''),1500))]).then(p=>sendLog(JSON.stringify({{rosticceria:name,dispositivo:deviceLabel(),posizione:p||''}})))}}const dlg=document.querySelector('#detail');let cur=0;function openCard(i,dir){{cur=i;const s=DATA.shops[i],r=DATA.results[i];
/* cornice doppia della scheda: verde se il menu di oggi è pubblicato, arancione se no (stessi colori delle schede) */
const n0=new Date(),iso0=n0.getFullYear()+'-'+String(n0.getMonth()+1).padStart(2,'0')+'-'+String(n0.getDate()).padStart(2,'0');dlg.classList.toggle('ok',r.menu_date===iso0);dlg.classList.toggle('old',r.menu_date!==iso0);logClick(s.nome);document.querySelector('#name').textContent=s.nome+(r.menu_date?' - '+r.menu_date.split('-').reverse().join('/'):'');const ad=document.querySelector('#addr');ad.textContent=s.indirizzo||'';ad.hidden=!s.indirizzo;const img=document.querySelector('#full');img.src=r.image?r.image+'?v='+DATA.v:'';img.hidden=!r.image;const tx=document.querySelector('#text');tx.innerHTML='';(r.sections||[]).forEach(sec=>{{const h=document.createElement('h3');h.textContent=sec.titolo;tx.append(h);sec.piatti.forEach(p=>{{const d=document.createElement('div');d.className='dish';d.innerHTML='<span></span><b></b><small></small>';d.children[0].textContent=p.nome;d.children[1].textContent=p.prezzo;d.children[2].textContent=p.descrizione;tx.append(d)}})}});document.querySelector('#nomenu').hidden=!!(r.image||(r.sections||[]).length);document.querySelector('#meta').textContent=r.nota||(r.menu_date?'':'Menu non disponibile');const phone=document.querySelector('#phone');phone.hidden=!s.telefono;phone.textContent=s.telefono||'';phone.href='tel:'+(s.telefono||'').replace(/[^+\\d]/g,'');const map=document.querySelector('#map');map.hidden=!s.indirizzo;map.href='https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(s.nome.replace(/\\s*\\(.*\\)/,'')+', '+(s.indirizzo||''));const source=document.querySelector('#source');source.hidden=!s.url;source.href=s.url||'';if(!dlg.open)dlg.showModal();dlg.scrollTop=0;
if(dir)['#full','#text','#nomenu','.modal-head'].forEach(q=>{{const e=document.querySelector(q);if(e&&e.animate)e.animate([{{opacity:.3,transform:'translateX('+(dir*40)+'px)'}},{{opacity:1,transform:'none'}}],{{duration:200,easing:'ease-out'}})}})}}
/* scheda precedente / successiva in modo circolare: frecce ai lati, tasti ← → e, sul telefono,
   scorrimento del dito a destra o a sinistra */
function step(d){{const order=[...document.querySelectorAll('.card')].map(c=>+c.dataset.index),n=order.length;openCard(order[(order.indexOf(cur)+d+n)%n],d)}}
document.querySelector('#prev').onclick=e=>{{e.stopPropagation();step(-1)}};document.querySelector('#next').onclick=e=>{{e.stopPropagation();step(1)}};
document.addEventListener('keydown',e=>{{if(!dlg.open)return;if(e.key==='ArrowLeft'){{e.preventDefault();step(-1)}}else if(e.key==='ArrowRight'){{e.preventDefault();step(1)}}}});
let tx=null,ty=0;dlg.addEventListener('touchstart',e=>{{if(e.touches.length!==1){{tx=null;return}}tx=e.touches[0].clientX;ty=e.touches[0].clientY}},{{passive:true}});
dlg.addEventListener('touchmove',e=>{{if(e.touches.length!==1)tx=null}},{{passive:true}});
dlg.addEventListener('touchend',e=>{{if(tx===null)return;const t=e.changedTouches[0],dx=t.clientX-tx,dy=t.clientY-ty;tx=null;
if(Math.abs(dx)>50&&Math.abs(dx)>1.5*Math.abs(dy)&&!(window.visualViewport&&visualViewport.scale>1.05))step(dx<0?1:-1)}});document.querySelectorAll('.card').forEach(c=>{{c.onclick=()=>openCard(+c.dataset.index)}});document.querySelector('#close').onclick=()=>dlg.close();dlg.onclick=e=>{{if(e.target===dlg)dlg.close()}};function refresh(){{const n=new Date(),iso=n.getFullYear()+'-'+String(n.getMonth()+1).padStart(2,'0')+'-'+String(n.getDate()).padStart(2,'0');const gg=['Domenica','Lunedì','Martedì','Mercoledì','Giovedì','Venerdì','Sabato'],mm=['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto','settembre','ottobre','novembre','dicembre'];const t=DATA.titolo.replace('{{data}}',gg[n.getDay()]+' '+n.getDate()+' '+mm[n.getMonth()]);document.querySelector('h1').textContent=t;document.querySelectorAll('.card').forEach(c=>{{const i=+c.dataset.index,r=DATA.results[i],ok=r.menu_date===iso,st=c.querySelector('.status');c.classList.toggle('band-ok',ok);c.classList.toggle('band-old',!ok);st.className='status '+(ok?'fresh':r.menu_date?'stale':'missing');st.textContent=ok?'Oggi':r.menu_date?'Non di oggi':(r.error?'Errore':'Non disponibile')}});
/* ordine: prima le rosticcerie "Oggi" (fascia verde: menu, avviso di chiusura o riposo di oggi),
   poi le altre; in ciascun gruppo in ordine alfabetico. Rifatto anche quando cambia il giorno. */
const main=document.querySelector('main'),col=new Intl.Collator('it',{{sensitivity:'base'}}),has=i=>DATA.results[i].menu_date===iso;
[...document.querySelectorAll('.card')].sort((a,b)=>{{const x=+a.dataset.index,y=+b.dataset.index;return (has(y)-has(x))||col.compare(DATA.shops[x].nome,DATA.shops[y].nome)}}).forEach(c=>main.insertBefore(c,main.querySelector('.infocard')))}}refresh();let loaded=Date.now();document.addEventListener('visibilitychange',()=>{{if(document.visibilityState!=='visible')return;refresh();if(Date.now()-loaded>300000)location.reload()}})
/* Solo con ?v=57 nell'indirizzo: click di oggi in basso a destra di ogni scheda, totale nella scheda "Info";
   un tocco su "Info" apre la tabella Rosticceria / Oggi / Mese / Anno / Tutto con i totali.
   I numeri arrivano da Registro_click.gs (?azione=statistiche). Per non far aspettare, l'ultimo
   risultato è conservato sul dispositivo e mostrato subito, poi aggiornato appena arriva quello nuovo. */
if(ADMIN&&DATA.log){{const info=document.createElement('div');info.className='infocard';info.tabIndex=0;info.setAttribute('role','button');info.innerHTML='<h2>Info</h2><div class="total">–</div><small>click di oggi</small>';document.querySelector('main').append(info);
const sd=document.createElement('dialog');sd.id='stats';sd.innerHTML='<div class="modal-head"><h2>Click per rosticceria</h2><button class="x" aria-label="Chiudi">✕</button></div><div class="statwrap"><table><thead><tr><th>Rosticceria</th><th>Oggi</th><th>Mese</th><th>Anno</th><th>Tutto</th></tr></thead><tbody></tbody><tfoot></tfoot></table><p class="statnote"></p></div>';document.body.append(sd);
sd.querySelector('.x').onclick=()=>sd.close();sd.addEventListener('click',e=>{{if(e.target===sd)sd.close()}});
let ST=null,loading=false;try{{ST=JSON.parse(localStorage.getItem('menuStats')||'null')}}catch(e){{}}
const P=['oggi','mese','anno','tutto'],today=()=>new Date().toLocaleDateString('sv-SE',{{timeZone:'Europe/Rome'}});
function rowOf(d,n){{const ns=[n].concat((DATA.shops.find(s=>s.nome===n)||{{}}).nomi_precedenti||[]);const z={{}};P.forEach(k=>z[k]=0);ns.forEach(x=>{{const r=(d.righe||{{}})[x]||{{}};P.forEach(k=>z[k]+=r[k]||0)}});if(d.data!==today()){{z.oggi=0}}return z}}
function cell(t,v){{const c=document.createElement(t);c.textContent=v;return c}}
function render(){{if(!ST)return;document.querySelectorAll('.card').forEach(card=>{{const b=card.querySelector('.count');b.textContent=rowOf(ST,DATA.shops[+card.dataset.index].nome).oggi;b.hidden=false}});
const names=DATA.shops.map(s=>s.nome).sort(new Intl.Collator('it',{{sensitivity:'base'}}).compare),old=DATA.shops.flatMap(s=>s.nomi_precedenti||[]);Object.keys(ST.righe||{{}}).forEach(n=>{{if(!names.includes(n)&&!old.includes(n))names.push(n)}});const tot={{oggi:0,mese:0,anno:0,tutto:0}};
const tb=sd.querySelector('tbody');tb.innerHTML='';names.forEach(n=>{{const z=rowOf(ST,n);const tr=document.createElement('tr');tr.append(cell('td',n));P.forEach(k=>{{tot[k]+=z[k];tr.append(cell('td',z[k]))}});tb.append(tr)}});
const tf=sd.querySelector('tfoot');tf.innerHTML='';const tr=document.createElement('tr');tr.append(cell('td','Totale'));P.forEach(k=>tr.append(cell('td',tot[k])));tf.append(tr);
info.querySelector('.total').textContent=tot.oggi;sd.querySelector('.statnote').textContent=(loading?'Aggiornamento in corso… ':'')+(ST.ora?'Dati delle '+ST.ora:'')}}
function loadStats(){{if(loading)return;loading=true;render();fetch(DATA.log+'?azione=statistiche&t='+Date.now(),{{cache:'no-store'}}).then(r=>r.json()).then(d=>{{if(!d||!d.righe)return;d.ora=new Date().toLocaleTimeString('it-IT',{{hour:'2-digit',minute:'2-digit'}});ST=d;try{{localStorage.setItem('menuStats',JSON.stringify(d))}}catch(e){{}}}}).catch(()=>{{}}).finally(()=>{{loading=false;render()}})}}
function openStats(){{render();sd.showModal();loadStats()}}info.addEventListener('click',openStats);info.addEventListener('keydown',e=>{{if(e.key==='Enter'||e.key===' '){{e.preventDefault();openStats()}}}});
render();loadStats();setInterval(loadStats,60000);document.addEventListener('visibilitychange',()=>{{if(document.visibilityState==='visible')loadStats()}})}}</script>
</body></html>'''
    OUTPUT.write_text(document, encoding="utf-8")
    WEB_PAGE.write_text(document, encoding="utf-8")




def make_icons(force: bool = False) -> bool:
    """Ricava le icone dell'app da loghi/Menu.jpg se il logo è più recente delle icone.

    Restituisce True se le icone sono state rigenerate."""
    if not APP_LOGO.exists():
        return False
    newest = ICONS / "icona-512.png"
    if not force and newest.exists() and newest.stat().st_mtime >= APP_LOGO.stat().st_mtime:
        return False
    try:
        from PIL import Image
    except ImportError:
        print("Icone non aggiornate: manca Pillow (pip install pillow).")
        return False
    ICONS.mkdir(exist_ok=True)
    with Image.open(APP_LOGO) as logo:
        logo = logo.convert("RGB")
        side = min(logo.size)
        left, top = (logo.width - side) // 2, (logo.height - side) // 2
        square = logo.crop((left, top, left + side, top + side))
        for name, size in ICON_SIZES.items():
            square.resize((size, size), Image.LANCZOS).save(ICONS / name, optimize=True)
    print("Icone dell'app rigenerate da loghi/Menu.jpg")
    return True


LOGO_SIDE = 160  # pixel della miniatura (mostrata a 64 px: nitida anche sugli schermi Retina)


def shop_logo_source(shop: dict[str, Any]) -> Path | None:
    """Logo del locale: il file indicato in locali.json ("logo": "fantasia.jpg", cercato nella
    cartella loghi), altrimenti
    loghi/<id>.jpg|.jpeg|.png|.webp. None se non c'è."""
    if shop.get("logo"):
        name = Path(shop["logo"])
        for path in ((name,) if name.is_absolute() else (LOGOS / name, ROOT / name)):
            if path.is_file():
                return path
    for extension in (".jpg", ".jpeg", ".png", ".webp"):
        path = LOGOS / f"{shop['id']}{extension}"
        if path.is_file():
            return path
    return None


def make_shop_logos(shops: list[dict[str, Any]]) -> dict[str, str]:
    """Miniature quadrate dei loghi in icone/logo-<id>.jpg (leggere: pochi KB l'una), rifatte
    solo quando il logo cambia. Il logo intero viene centrato nel quadrato (non tagliato), con
    il colore del bordo del logo come sfondo. Restituisce {id: "icone/logo-<id>.jpg?v=..."}."""
    logos: dict[str, str] = {}
    try:
        from PIL import Image
    except ImportError:
        print("Loghi non aggiornati: manca Pillow (pip install pillow).")
        Image = None
    ICONS.mkdir(exist_ok=True)
    for shop in shops:
        source = shop_logo_source(shop)
        target = ICONS / f"logo-{shop['id']}.jpg"
        if source and Image and (not target.exists() or target.stat().st_mtime < source.stat().st_mtime):
            try:
                with Image.open(source) as picture:
                    picture = picture.convert("RGBA")
                    flat = Image.new("RGB", picture.size, "white")
                    flat.paste(picture, mask=picture.split()[3])
                    side = max(flat.size)
                    square = Image.new("RGB", (side, side), flat.getpixel((0, 0)))
                    square.paste(flat, ((side - flat.width) // 2, (side - flat.height) // 2))
                    square.resize((LOGO_SIDE, LOGO_SIDE), Image.LANCZOS).save(target, quality=88, optimize=True)
            except Exception as error:
                print(f"  {shop['nome']} - logo non leggibile ({source.name}): {error}")
        if target.exists():
            logos[shop["id"]] = f"icone/{target.name}?v={file_hash(target)[:8]}"
    return logos


def publish() -> None:
    """Invia a GitHub (repository Menu) la pagina e i menu correnti.

    Prima di inviare scarica le eventuali modifiche fatte sul sito di GitHub
    (es. un logo caricato dal browser). In caso di conflitto prevale il PC."""
    import subprocess

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def commit() -> None:
        git("add", "-A")
        if git("status", "--porcelain").stdout.strip():
            git("commit", "-m", f"Menu {datetime.now():%Y-%m-%d %H:%M}")

    if not (ROOT / ".git").exists():
        print("Pubblicazione saltata: la cartella non è ancora collegata a GitHub (vedi LEGGIMI.txt).")
        return
    commit()
    pulled = git("pull", "--rebase", "-X", "theirs")
    if pulled.returncode != 0:
        git("rebase", "--abort")
        print("Pubblicazione NON riuscita (download da GitHub):\n" + (pulled.stderr or pulled.stdout).strip())
        return
    if make_icons():  # il logo può essere arrivato da GitHub
        commit()
    # quanti invii locali non sono ancora su GitHub (non dipende dalla lingua di git)
    ahead = git("rev-list", "--count", "origin/main..HEAD").stdout.strip()
    if ahead == "0":
        print("Pubblicazione: nessuna modifica da inviare.")
        return
    pushed = git("push", "-u", "origin", "main")
    if pushed.returncode != 0:
        print("Pubblicazione NON riuscita:\n" + pushed.stderr.strip())
    else:
        print(f"Pubblicato: {WEB_URL}")


def add_shop(config: dict[str, Any]) -> None:
    print("\nNuova rosticceria (INVIO lascia vuoto)")
    name = input("Nome: ").strip()
    if not name:
        raise SystemExit("Nome obbligatorio.")
    shop_id = slugify(input(f"Identificativo [{slugify(name)}]: ").strip() or name)
    shop = {
        "id": shop_id,
        "nome": name,
        "telefono": input("Telefono: ").strip(),
        "indirizzo": input("Indirizzo: ").strip(),
        "url": input("Pagina Facebook, Instagram o sito: ").strip(),
        "logo": "",
        "fonti": [{"tipo": "cartella", "attiva": True}],
    }
    config.setdefault("locali", []).append(shop)
    (INPUT / shop_id).mkdir(parents=True, exist_ok=True)
    save_json(CONFIG, config)
    print(f"Creato ingresso/{shop_id}. Inserisci lì una foto del menu e rilancia Menu.py.")


AUTO_LOG = DATA / "automatico.log"
AUTO_LOCK = DATA / "automatico.lock"


def in_time_window(settings: dict[str, Any]) -> bool:
    """True se adesso è dentro la fascia "automatico" di locali.json (predefinita 08:00-14:00)."""
    window = settings.get("automatico", {})
    now = datetime.now().strftime("%H:%M")
    return window.get("dalle", "08:00") <= now <= window.get("alle", "14:00")


def start_auto_log() -> None:
    """In modalità automatica (pythonw, senza finestra) scrive i messaggi in dati/automatico.log."""
    DATA.mkdir(exist_ok=True)
    if AUTO_LOG.exists() and AUTO_LOG.stat().st_size > 500_000:  # tiene il log piccolo
        lines = AUTO_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
        AUTO_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stream = AUTO_LOG.open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===")


def kill_previous_run() -> None:
    """Chiude un giro precedente rimasto bloccato (con il suo browser e le sue finestre)."""
    try:
        pid = int(AUTO_LOCK.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if pid == os.getpid() or sys.platform != "win32":
        return
    import subprocess
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(f"Chiuso un controllo precedente rimasto bloccato (processo {pid}).")
    AUTO_LOCK.unlink(missing_ok=True)


def take_lock(max_wait_minutes: int = 20) -> bool:
    """Evita due esecuzioni contemporanee (giro automatico e giro manuale).

    Se un altro giro è in corso aspetta che finisca (al massimo max_wait_minutes), così
    l'orario fisso del controllo automatico viene rispettato anche dopo un controllo
    manuale. Un blocco più vecchio di 30 minuti è considerato abbandonato."""
    import time
    if AUTO_LOCK.exists() and time.time() - AUTO_LOCK.stat().st_mtime >= 30 * 60:
        kill_previous_run()
    waited = False
    deadline = time.time() + max_wait_minutes * 60
    while AUTO_LOCK.exists() and time.time() - AUTO_LOCK.stat().st_mtime < 30 * 60:
        if time.time() > deadline:
            kill_previous_run()  # aspettato troppo: chiudo il giro bloccato e parto
            break
        if not waited:
            print("Un altro controllo è in corso: attendo che finisca...")
            waited = True
        time.sleep(5)
    AUTO_LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def menu_fingerprints(results: list[dict[str, Any]]) -> dict[str, str]:
    """Impronta del menu pubblicato per ogni locale (data + contenuto immagine o testo)."""
    prints = {}
    for result in results:
        image = ROOT / result["image"] if result.get("image") else None
        if not (image and image.exists()) and not result.get("sections"):
            continue  # nessun menu pubblicato per questo locale
        content = file_hash(image) if image and image.exists() else json.dumps(result.get("sections"), sort_keys=True)
        prints[result["id"]] = hashlib.sha1(f"{result.get('menu_date')}|{content}".encode()).hexdigest()
    return prints


def beep_three_times() -> None:
    """Tre beep bassi a un secondo di distanza (solo Windows)."""
    try:
        import time
        import winsound
    except ImportError:
        return
    for index in range(3):
        winsound.Beep(250, 400)  # 250 Hz = tono basso, 0,4 secondi
        if index < 2:
            time.sleep(0.6)       # 0,4 + 0,6 = un beep ogni secondo


def main() -> None:
    parser = argparse.ArgumentParser(description="Raccoglie e pubblica menu da fonti configurabili.")
    parser.add_argument("--aggiungi", action="store_true", help="Aggiunge una rosticceria con procedura guidata.")
    parser.add_argument("--solo-html", action="store_true", help="Non prova fonti online; usa i file disponibili.")
    parser.add_argument("--visibile", action="store_true", help="Mostra il browser durante le acquisizioni online.")
    parser.add_argument("--pubblica", action="store_true", help="Dopo l'aggiornamento invia la pagina a GitHub Pages.")
    parser.add_argument("--salta-aggiornati", action="store_true",
                        help="Non ricontrolla online le rosticcerie che hanno già il menu di oggi "
                             "(pulsante \"Controlla\" della finestra di controllo; il giro ogni 15 minuti fa lo stesso).")
    parser.add_argument("--login", action="store_true", help="Apre Facebook e Instagram per salvare la sessione.")
    parser.add_argument("--prova-beep", action="store_true", help="Fa sentire i tre beep e termina.")
    parser.add_argument("--ocr", metavar="IMMAGINE", help="Mostra il testo letto in una foto e l'eventuale chiusura.")
    parser.add_argument("--automatico", action="store_true",
                        help="Per l'attività pianificata: solo nella fascia oraria, senza finestre, con log e pubblicazione.")
    args = parser.parse_args()
    if args.prova_beep:
        beep_three_times()
        return
    if args.ocr:
        text = ocr_image(Path(args.ocr))
        print(f"Testo letto: {text or '(nessuno)'}")
        closed = closure_period(text)
        print(f"Chiusura: dal {closed[0]:%d/%m} al {closed[1]:%d/%m}" if closed else "Chiusura: nessuna")
        return
    make_folders()
    config = read_json(CONFIG, {"impostazioni": {}, "locali": []})
    if args.automatico:
        if not in_time_window(config.get("impostazioni", {})):
            return  # fuori fascia: nessun accesso, nessun log
        start_auto_log()
        if not take_lock():
            print("Esecuzione precedente bloccata da oltre 20 minuti: salto questo giro.")
            return
        args.pubblica, args.solo_html, args.visibile, args.aggiungi, args.login = True, False, False, False, False
        try:
            run(args, config)
        except BaseException as exc:  # nel log, non in una finestra che nessuno vede
            import traceback
            print(f"ERRORE: {exc!r}\n{traceback.format_exc()}")
        finally:
            AUTO_LOCK.unlink(missing_ok=True)
        return
    if args.login or args.aggiungi:
        run(args, config)
        return
    start_manual_log("controllo" if args.salta_aggiornati else "ricontrollo")
    if not take_lock():
        print("Un altro controllo è bloccato da oltre 20 minuti: riprova più tardi.")
        return
    try:
        run(args, config)
    finally:
        AUTO_LOCK.unlink(missing_ok=True)


class ResultsOnly:
    """Per la finestra DOS: lascia passare solo le righe dei risultati (es. "Fantasia: 2026-10-05
    (cartella)") e l'esito della pubblicazione. I dettagli (righe rientrate: testo letto, avvisi...)
    e "Creato: ..." finiscono solo nel registro dati/automatico.log."""

    def __init__(self, stream: Any) -> None:
        self.stream, self.pending = stream, ""

    def write(self, text: str) -> int:
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.strip() and not line[:1].isspace() and not line.startswith("Creato:"):
                self.stream.write(line + "\n")
        return len(text)

    def flush(self) -> None:
        if self.pending and not self.pending[:1].isspace():  # es. domanda di input() senza a capo
            self.stream.write(self.pending)
            self.pending = ""
        self.stream.flush()


class Tee:
    """Scrive contemporaneamente a video e nel registro."""

    def __init__(self, *streams: Any) -> None:
        self.streams = [stream for stream in streams if stream is not None]

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def start_manual_log(kind: str = "ricontrollo") -> None:
    """Anche i giri lanciati a mano (Avvia.bat) finiscono in dati/automatico.log."""
    DATA.mkdir(exist_ok=True)
    stream = AUTO_LOG.open("a", encoding="utf-8", buffering=1)
    sys.stdout = Tee(ResultsOnly(sys.__stdout__) if sys.__stdout__ else None, stream)
    sys.stderr = Tee(sys.__stderr__, stream)  # gli errori si vedono sempre per intero
    print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} === (manuale, {kind})")
    print("CONTROLLO: ricontrolla solo le rosticcerie non ancora aggiornate oggi" if kind == "controllo"
          else "RICONTROLLO: ricontrolla tutte le rosticcerie, anche quelle già aggiornate")


def run(args: argparse.Namespace, config: dict[str, Any]) -> None:
    if args.aggiungi:
        add_shop(config)
        config = read_json(CONFIG, config)
    shops = config.get("locali", [])
    settings = config.get("impostazioni", {})
    ids = [shop.get("id") for shop in shops]
    if not shops or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise SystemExit("locali.json non valido: servono locali con id univoci.")
    browser = BrowserCollector(visible=args.visibile or args.login)
    if args.login:
        browser.login()
        browser.stop()
        return
    global NOTICE_MINUTES
    NOTICE_MINUTES = int(settings.get("avvisi_ogni_minuti", NOTICE_MINUTES))
    results: list[dict[str, Any]] = []
    previous_sources = {item.get("id"): item.get("source", "") for item in read_json(STATE, {}).get("results", [])}
    # stesso ordine della finestra di controllo e del sito: prima quelle già aggiornate oggi
    # (secondo l'ultimo giro), poi le altre; in ciascun gruppo in ordine alfabetico
    previous_days = {item.get("id"): item.get("menu_date", "") for item in read_json(STATE, {}).get("results", [])}
    shops = sorted(shops, key=lambda item: (previous_days.get(item["id"]) != date.today().isoformat(),
                                            sort_key_name(item.get("nome", ""))))
    try:
        for shop in shops:
            previous = previous_sources.get(shop["id"]) or "già acquisito oggi"
            result = acquire(shop, browser, online=not args.solo_html,
                             skip_if_today=previous if args.automatico or args.salta_aggiornati else None)
            show_old = bool(settings.get("mostra_menu_vecchi", False))
            publishable = bool(result.image and result.menu_day and (show_old or result.menu_day == date.today()))
            current = copy_current(shop, result.image, result.menu_day) if publishable else None
            if not publishable:
                remove_current(shop)
            record = {
                "id": shop["id"], "image": current.relative_to(ROOT).as_posix() if current else "",
                "menu_date": result.menu_day.isoformat() if result.menu_day else "", "source": result.source,
                "checked_at": result.checked_at, "error": result.error,
            }
            if current and closure_note(result.image):
                record["nota"] = closure_note(result.image)
            if current and current.suffix.lower() == TEXT_EXTENSION:
                record["image"] = ""
                record["sections"] = read_json(current, {}).get("sezioni", [])
            results.append(record)
            print(f"{shop['nome']}: {record['menu_date'] or 'nessun menu'} ({result.source})")
    finally:
        browser.stop()
    old_state = read_json(STATE, {})
    prints = menu_fingerprints(results)
    names = {shop["id"]: shop["nome"] for shop in shops}
    # variazioni = menu nuovo o cambiato, oppure menu di oggi tolto dalla pagina.
    # I menu dei giorni precedenti che scadono non contano (niente beep al primo giro del mattino).
    news: list[str] = []
    if "firme" in old_state:
        old_prints = old_state["firme"]
        old_days = {item.get("id"): item.get("menu_date") for item in old_state.get("results", [])}
        news = [names[key] for key, value in prints.items() if old_prints.get(key) != value]
        news += [f"{names[key]} (tolto)" for key in old_prints
                 if key not in prints and key in names and old_days.get(key) == date.today().isoformat()]
    state = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "results": results, "firme": prints}
    save_json(STATE, state)
    make_icons()
    generate_html(settings, shops, results)
    print(f"Creato: {OUTPUT}")
    if args.pubblica:
        publish()
    if news:
        print("Novità: " + ", ".join(news))  # anche la finestra di controllo legge questa riga
        beep_three_times()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nOperazione annullata.")
