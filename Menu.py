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
# stessa pagina per l'amministratore: aperta con ?v=57 la pagina passa qui, così "+Home" salva
# un indirizzo che contiene già l'amministratore (iPhone a volte perde la parte "?v=57")
ADMIN_PAGE = ROOT / "admin.html"
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


AUXILIARY_FILES = ("_avviso_controllo",)  # file di servizio: non sono mai un menu


def newest_image(folder: Path) -> Path | None:
    images = [item for item in folder.glob("*") if item.is_file() and item.suffix.lower() in MENU_EXTENSIONS
              and not any(mark in item.stem for mark in AUXILIARY_FILES)]
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
 const hit = [...document.querySelectorAll('*')].find(el => {
   const t = (el.innerText || el.textContent || '').trim();
   if (!t || t.length > 80 || !re.test(t)) return false;
   const r = el.getBoundingClientRect();
   return r.width > 0 && r.height > 0 && r.height < 60 && r.top >= 0 && r.top < 140
     && r.left > innerWidth * .35 && r.left < innerWidth;
 });
 if (!hit) return '';
 // intestazione della storia: risalgo dall'età finché compare anche il nome del profilo
 let box = hit, head = (hit.innerText || '').trim();
 for (let i = 0; i < 6 && box.parentElement; i++) {
   box = box.parentElement;
   const t = (box.innerText || '').trim();
   if (t.length > 160) break;
   head = t;
 }
 return head;
}"""


# nome del profilo visibile in alto nel visualizzatore della storia (seconda verifica del proprietario)
STORY_OWNER_JS = """(name) => {
 const norm = s => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
 const want = norm(name);
 return [...document.querySelectorAll('a, span, strong, h2, h3')].some(el => {
   if (norm(el.innerText) !== want) return false;
   const r = el.getBoundingClientRect();
   return r.width > 0 && r.height > 0 && r.top >= 0 && r.top < 160 && r.left > innerWidth * .3;
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
        self.last_posted: date | None = None  # giorno di pubblicazione del post letto (se si capisce)

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
        try:
            self.context = self.playwright.chromium.launch_persistent_context(str(profile), **options)
        except Exception as exc:
            if profile == PROFILE:
                raise
            # profilo collegato occupato (es. Stato.py lo sta usando): ripiego sul profilo normale
            print(f"  profilo {profile.name} non disponibile ({str(exc).splitlines()[0][:80]}): uso il profilo normale")
            options.pop("channel", None)
            options.pop("args", None)
            options["headless"] = not self.visible
            profile, profile_key = PROFILE, str(PROFILE.resolve())
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
        story_day: date | None = None
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
                    # età scritta accanto al nome del profilo ("7 h", "15 min"): si cerca dopo il nome
                    user = source.get("utente", "")
                    near = visible_text[visible_text.find(user):] if user and user in visible_text else visible_text
                    story_day = story_age_day(near)
                    if story_day is None:
                        raise RuntimeError("la Storia non risulta pubblicata nelle ultime 24 ore")
                    if story_day < date.today():
                        # pubblicata ieri (es. letta dopo mezzanotte): NON è il menu di oggi.
                        # browser_result la salva con la sua data e toglie un eventuale "menu di oggi" sbagliato
                        print(f"  {shop['nome']} - la storia è stata pubblicata il {story_day:%d/%m}: non è di oggi")
            selector = source.get("selettore", "")
            # riquadro del post (es. [aria-posinset='1']): si scorre finché compare IL POST, non
            # la sua foto. Un post solo testo (es. "MENÙ DI GIOVEDÌ" scritto nel post) non ha
            # foto: cercare la foto faceva scorrere la pagina fino in fondo, il post spariva
            # dalla pagina e il menu non veniva letto.
            box = re.match(r"\s*(\[aria-posinset='\d+'\])", selector or "")
            container = box.group(1) if box else ""
            if selector and source.get("scorri"):
                # Facebook carica i post solo scorrendo la pagina
                for _ in range(int(source.get("scorri_max", 12))):
                    if page.locator(container or selector).count():
                        break
                    page.mouse.wheel(0, 1200)
                    page.wait_for_timeout(1500)
                if container and page.locator(container).count():
                    try:
                        page.locator(container).first.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                    for _ in range(4):  # la foto del post può arrivare un attimo dopo il testo
                        if page.locator(selector).count():
                            break
                        page.wait_for_timeout(1000)
            self.last_text = ""
            self.last_posted = story_day  # giorno della storia (se è una storia), altrimenti None
            if source.get("selettore_testo"):
                expand_more(page, source["selettore_testo"])
                for _ in range(3):
                    try:
                        self.last_text = clean_post_text(
                            page.locator(source["selettore_testo"]).first.inner_text(timeout=3000))
                    except Exception:
                        pass
                    if self.last_text.strip():
                        break
                    page.wait_for_timeout(1000)
            # giorno di pubblicazione: intestazione del post ("6 ore fa", "ieri alle 18:30"...),
            # cioè il testo del riquadro del post PRIMA del messaggio
            if container and page.locator(container).count():
                try:
                    whole = page.locator(container).first.inner_text(timeout=3000)
                    first_line = next((line.strip() for line in self.last_text.splitlines() if line.strip()), "")
                    header = whole.split(first_line[:40])[0] if first_line and first_line[:40] in whole else whole[:150]
                    self.last_posted = post_day(header)
                except Exception:
                    pass
            if selector and not page.locator(selector).count():
                if self.last_text.strip():
                    self.last_alt = ""
                    return None  # post solo testo (es. menu scritto nel post): lo gestisce acquire
                seen_box = ""
                if container and page.locator(container).count():
                    try:  # cosa c'è nel post, per capire dal registro perché non si legge
                        seen_box = " ".join(line.strip() for line in page.locator(container).first.inner_text(timeout=2000).splitlines()
                                            if line.strip() and line.strip() != "Facebook")[:120]
                    except Exception:
                        pass
                raise RuntimeError(f"nessun elemento trovato con il selettore {selector}"
                                   + (f" (nel post: {seen_box})" if seen_box else " (post non caricato)"))
            target = page.locator(selector).first if selector else self._largest_media(page)
            if target is None:
                raise RuntimeError("nessuna immagine grande visibile")
            # Facebook scrive nel testo alternativo dell'immagine le parole che legge nella foto
            # (es. "...il seguente testo: 'Siamo chiusi da domenica 4/10 a mercoledì 7/10'")
            try:
                self.last_alt = target.get_attribute("alt", timeout=2000) or ""
            except Exception:
                self.last_alt = ""
            CAPTURES.mkdir(parents=True, exist_ok=True)
            destination = CAPTURES / f"{shop['id']}.jpg"  # va in ingresso solo se è un menu
            save_media(self.context, page, target, destination)
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
        self.start(with_facebook_login({"url": url}))
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
                # foto usata solo per cercare un avviso di chiusura: va in dati/catture,
                # MAI in ingresso (se il programma si interrompe non deve sembrare un menu)
                CAPTURES.mkdir(parents=True, exist_ok=True)
                image = CAPTURES / f"{shop['id']}_avviso_controllo.jpg"
                try:
                    save_media(self.context, page, target, image)
                except Exception:
                    image = None
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
                age = ""
                for _ in range(15):
                    age = page.evaluate(STORY_RECENT_JS) or ""
                    if age:
                        break
                    page.wait_for_timeout(300)
                owner = source.get("nome_profilo", shop["nome"])
                if age and plain(owner) not in plain(age) and not page.evaluate(STORY_OWNER_JS, owner):
                    # Facebook è passato da solo alla storia di un ALTRO profilo (es. "Stefy Giorgio"):
                    # le parti della storia del locale sono finite
                    print(f"  {shop['nome']} - parte {part + 1}: è la storia di un altro profilo "
                          f"({' '.join(age.split())[:40]}), mi fermo")
                    break
                if story_age_day(age) != date.today():
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
            # nessuna foto buona adesso: una foto salvata stamattina viene ritrovata dalla fonte
            # "cartella" (con i suoi controlli); qui si tolgono solo le anteprime sfocate
            if destination.exists() and not image_is_sharp(destination.read_bytes()):
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

    def snapshot_source(self, shop: dict[str, Any], source: dict[str, Any]) -> bytes | None:
        """Fotografia (JPEG) della fonte così come appare adesso: il post Facebook, la storia
        Facebook/Instagram aperta, il sito del locale. Serve alla Panorama; non salva nulla."""
        self.start(source)
        page = self.context.new_page()
        try:
            page.goto(source["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(int(source.get("attesa_secondi", 6)) * 1000)
            dismiss_dialogs(page)
            kind = source.get("tipo", "")
            if kind == "storia_facebook" or source.get("apri_storia"):
                if source.get("utente"):  # Instagram: clic sulla foto del profilo
                    link = page.locator(f'img[alt*="profilo di {source["utente"]}" i]').first.locator("..")
                else:
                    link = page.locator("a[href*='/stories/']:not([href*='/stories/create'])"
                                        ":not([href*='/highlights/'])").filter(visible=True).first
                try:
                    link.click(timeout=8000)
                    page.wait_for_timeout(5000)
                    for text in ("Clicca per visualizzare la storia", "Click to view story"):
                        button = page.get_by_text(text, exact=True).first
                        if button.is_visible():
                            button.click(timeout=2000)
                            page.wait_for_timeout(2500)
                    pause = page.get_by_role("button", name=re.compile(r"^(Metti in pausa|Pausa|Pause)$")).first
                    if pause.is_visible():
                        pause.click(timeout=1500)
                except Exception:
                    pass  # nessuna storia attiva: si fotografa il profilo
            else:
                box = re.match(r"\s*(\[aria-posinset='\d+'\])", source.get("selettore", "") or "")
                if box:
                    for _ in range(int(source.get("scorri_max", 12))):
                        if page.locator(box.group(1)).count():
                            break
                        page.mouse.wheel(0, 1200)
                        page.wait_for_timeout(1500)
                    if page.locator(box.group(1)).count():
                        try:
                            page.locator(box.group(1)).first.scroll_into_view_if_needed(timeout=3000)
                            page.wait_for_timeout(1500)
                            return page.locator(box.group(1)).first.screenshot(type="jpeg", quality=80, timeout=10000)
                        except Exception:
                            pass
            return page.screenshot(type="jpeg", quality=80)
        except Exception as exc:
            print(f"  {shop['nome']} - fonte non fotografata: {str(exc).splitlines()[0][:90]}")
            return None
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


def save_media(context: Any, page: Any, target: Any, destination: Path) -> None:
    """Salva l'immagine (o il fotogramma del video) mostrata da target in destination.

    - indirizzo http(s): scarica il file originale;
    - video (es. storia Instagram con musica): usa l'immagine di copertina ("poster") se c'è,
      altrimenti il fotogramma corrente del video, pulito (senza i comandi sovrapposti);
    - indirizzo "blob:" o "data:" (Instagram/Facebook a volte mostrano così le foto): il file
      non si può scaricare da fuori, quindi lo legge la pagina stessa; se non riesce, fotografa
      l'elemento. Prima questo caso dava l'errore 'Protocol "blob:" not supported'."""
    info = target.evaluate("e => ({tag: e.tagName.toLowerCase(), src: e.currentSrc || e.src || '', poster: e.poster || ''})")
    url = info.get("poster") if info.get("tag") == "video" and info.get("poster") else info.get("src", "")
    if url.startswith(("http://", "https://")):
        try:
            response = context.request.get(url, timeout=30000)
            content_type = response.headers.get("content-type", "").split(";")[0].lower()
            if response.ok and content_type in {"image/jpeg", "image/png", "image/webp"}:
                destination.write_bytes(response.body())
                return
        except Exception:
            pass
    elif url.startswith(("blob:", "data:")) and info.get("tag") == "img":
        try:
            encoded = page.evaluate(
                """async url => { const blob = await (await fetch(url)).blob();
                     return await new Promise(ok => { const r = new FileReader();
                       r.onload = () => ok(String(r.result).split(',')[1] || ''); r.readAsDataURL(blob); }); }""",
                url)
            if encoded:
                import base64
                destination.write_bytes(base64.b64decode(encoded))
                return
        except Exception:
            pass
    if info.get("tag") in ("video", "img"):
        # fotogramma del video (o foto) "pulito", alla sua risoluzione: disegnato su una tela, quindi
        # SENZA le scritte e i pulsanti che Instagram/Facebook sovrappongono (nome del profilo,
        # musica, barra "Rispondi a...", adesivi). La fotografia dello schermo li includeva.
        for _ in range(6):
            try:
                encoded = page.evaluate(
                    """v => { const w = v.videoWidth || v.naturalWidth, h = v.videoHeight || v.naturalHeight;
                         if (!w || (v.tagName === 'VIDEO' && v.readyState < 2)) return '';
                         const c = document.createElement('canvas');
                         c.width = w; c.height = h;
                         c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
                         return c.toDataURL('image/jpeg', 0.92).split(',')[1] || ''; }""",
                    target.element_handle())
            except Exception:
                encoded = ""
            if encoded:
                import base64
                destination.write_bytes(base64.b64decode(encoded))
                return
            page.wait_for_timeout(500)  # il fotogramma può arrivare un attimo dopo
    target.screenshot(path=str(destination), type="jpeg", quality=92)


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


def text_post_result(shop: dict[str, Any], source: dict[str, Any], folder: Path, text: str, checked: str,
                     posted: date | None = None) -> Result:
    """Post senza foto: avviso di chiusura oppure menu scritto nel testo del post.
    Il menu viene salvato come AAAA-MM-GG_testo.json e mostrato riga per riga nella scheda."""
    name = source.get("nome", "browser")
    print(f"  {shop['nome']} - testo letto (post senza foto): {' '.join(text.split())[:160]}")
    learn_rest_days(shop, text)
    closed = closure_today(text)
    if closed:
        print(f"  {shop['nome']} - avviso di chiusura dal {closed[0]:%d/%m} al {closed[1]:%d/%m}")
        return Result(save_closure(folder, closed, None, text), date.today(), name, checked)
    clean = "\n".join(line.strip() for line in clean_post_text(text).splitlines() if line.strip())
    if not text_is_menu(text, source):
        # es. "Polpette fritte... Menu su Whatsapp": la parola "menu" da sola non basta.
        # Se in un giro precedente questo stesso testo era stato preso per un menu, lo tolgo
        for wrong in folder.glob("*_testo.json"):
            if read_json(wrong, {}).get("testo") == clean:
                DISCARDED.mkdir(parents=True, exist_ok=True)
                wrong.replace(DISCARDED / f"{shop['id']}_{wrong.name}")
                print(f"  {shop['nome']} - tolto {wrong.name} (non era un menu): ora in dati/scartati")
        raise NotAMenu("il post (solo testo) non sembra un menu")
    written_day = plausible_menu_date(clean)  # data o giorno scritto nel post ("MENÙ DI SABATO")
    for earlier in sorted(folder.glob("*_testo.json")):  # stesso testo già visto: resta la sua data
        if read_json(earlier, {}).get("testo") == clean:
            if written_day and written_day != image_date(earlier):
                corrected = folder / f"{written_day.isoformat()}_testo.json"
                earlier.replace(corrected)
                return Result(corrected, written_day, name, checked)
            return Result(earlier, image_date(earlier), name, checked)
    # senza data scritta vale il giorno di pubblicazione del post (un post di ieri non è di oggi)
    day = written_day or (posted if posted and posted < date.today() else date.today())
    lines = [line for line in clean.splitlines() if not line.lower().startswith(("altro", "mostra"))]
    path = folder / f"{day.isoformat()}_testo.json"
    save_json(path, {"data": day.isoformat(), "testo": clean, "sezioni": [
        {"titolo": "Menu del giorno", "piatti": [{"nome": line, "prezzo": "", "descrizione": ""} for line in lines]}]})
    return Result(path, day, name, checked)


MENU_WORDS = ["menu", "menù", "primi", "secondi", "contorni", "del giorno", "piatti", "antipasti"]

# frasi che da sole dicono "questo è il menu di un giorno" (non basta la parola "menù":
# "il menù è ricco e goloso, menu su Whatsapp" accompagna una foto pubblicitaria)
MENU_PHRASES = re.compile(
    r"men[uù]\s+(?:del\s+giorno|di\s+oggi|giornaliero|d['’]asporto|di\s+(?:luned|marted|mercoled|gioved|venerd|sabato|domenica))"
    r"|piatti\s+del\s+giorno|proposte\s+del\s+giorno|oggi\s+(?:trovate|abbiamo|vi\s+proponiamo)"
    # "Menù del / giorno GIOVEDÌ" (Impasta): l'OCR spesso perde "Menù del" scritto grande
    r"|\bgiorno\s+(?:luned|marted|mercoled|gioved|venerd|sabato|domenica)", re.IGNORECASE)
MENU_SECTIONS = re.compile(r"\b(?:antipasti|primi|secondi|contorni|dolci|frutta)\b", re.IGNORECASE)
POST_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
               "settembre", "ottobre", "novembre", "dicembre"]


def text_is_menu(text: str, source: dict[str, Any]) -> bool:
    """Il testo (del post o scritto nella foto) è un menu? Una frase tipo "menù del giorno",
    almeno due sezioni (primi, secondi, contorni...), una data, o una frase scelta per quel
    locale in "parole_menu" diversa dalle parole generiche (es. "oggi trovate")."""
    if MENU_PHRASES.search(text) or len(set(m.lower() for m in MENU_SECTIONS.findall(text))) >= 2:
        return True
    own = [word for word in source.get("parole_menu", []) if word not in MENU_WORDS]
    if any(word in text.lower() for word in own):
        return True
    return bool(plausible_menu_date(text)) and any(word in text.lower() for word in MENU_WORDS)


def text_lists_menu(text: str, source: dict[str, Any]) -> bool:
    """Il testo del post contiene davvero un menu (non solo la parola o una frase di invito):
    almeno due sezioni (primi, secondi, contorni...), una data o un giorno della settimana
    del menu, o una frase scelta per quel locale in "parole_menu"."""
    if len(set(m.lower() for m in MENU_SECTIONS.findall(text or ""))) >= 2 or plausible_menu_date(text or ""):
        return True
    own = [word for word in source.get("parole_menu", []) if word not in MENU_WORDS]
    return any(word in (text or "").lower() for word in own)


def photo_has_writing(photo_text: str, alt: str) -> bool:
    """La foto contiene scritte? (OCR con almeno 4 parole vere, o Facebook che la descrive con "testo")."""
    words = re.findall(r"[A-Za-zÀ-ÿ]{3,}", photo_text or "")
    return len(words) >= 4 or "testo" in (alt or "").lower()


def story_age_day(text: str) -> date | None:
    """Giorno di pubblicazione di una storia dalla sua età scritta accanto al nome:
    "adesso", "15 min", "7 h", "3 ore". Prima bastava "ultime 24 ore": dopo mezzanotte la storia
    di ieri mattina ("15 h") risultava di oggi. None se l'età non c'è (storia più vecchia o non letta)."""
    now = datetime.now()
    match = re.search(r"(?<![\w])(\d{1,2})\s*(min|minuti|minuto|m|h|ore|ora)(?![\w])|(?<![\w])(adesso)(?![\w])",
                      text or "", re.IGNORECASE)
    if not match:
        return None
    if match.group(3):
        return now.date()
    number, unit = int(match.group(1)), match.group(2).lower()
    return (now - (timedelta(hours=number) if unit in ("h", "ore", "ora") else timedelta(minutes=number))).date()


def post_day(header: str) -> date | None:
    """Giorno di pubblicazione da quello che Facebook scrive accanto al nome nel post:
    "20 minuti fa", "6 ore fa", "circa un'ora fa", "ieri alle 18:30", "un giorno fa", "2 giorni fa",
    "7 ottobre alle ore 12:00". Si guarda solo l'inizio del post (prima del testo)."""
    head = " ".join(header.split("\n")[:6]).lower()[:200]
    now = datetime.now()
    patterns = [
        (r"\b(\d+)\s*(?:min|minuti|minuto)\b", lambda n: now - timedelta(minutes=n)),
        (r"\b(\d+)\s*(?:h|ore|ora)\b", lambda n: now - timedelta(hours=n)),
        (r"\b(?:un['’]ora|circa un['’]ora)\b", lambda n: now - timedelta(hours=1)),
        (r"\bieri\b", lambda n: now - timedelta(days=1)),
        (r"\b(\d+)\s*(?:g|giorni)\b", lambda n: now - timedelta(days=n)),
        (r"\bun giorno fa\b", lambda n: now - timedelta(days=1)),
    ]
    for pattern, when in patterns:
        match = re.search(pattern, head)
        if match:
            number = int(match.group(1)) if match.groups() and match.group(1) else 0
            return when(number).date()
    match = re.search(r"\b(\d{1,2})\s+(" + "|".join(POST_MONTHS) + r")\b(?:\s+(20\d{2}))?", head)
    if match:
        year = int(match.group(3) or now.year)
        try:
            day = date(year, POST_MONTHS.index(match.group(2)) + 1, int(match.group(1)))
        except ValueError:
            return None
        return day if day <= now.date() else date(year - 1, day.month, day.day)
    return None


def plausible_menu_date(text: str) -> date | None:
    """Data del menu scritta nel post o nella foto (es. "Menu del giorno Sabato 3/10"),
    accettata solo se è tra 7 giorni fa e domani (evita prezzi o date lontane)."""
    if not text:
        return None
    for day in dates_in_text(fix_ocr_dates(text)):
        if -7 <= (day - date.today()).days <= 1:
            return day
    # nessuna data ma il giorno della settimana: "MENÙ DI SABATO", "Menu del giorno lunedì"
    # nomi interi ("giorno" non deve valere come "giovedì")
    match = re.search(r"\bmen[uù]\b[^\n]{0,25}?\b(luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)\b",
                      text.lower())
    if match:
        today = date.today()
        name = match.group(1)[:3]
        return today - timedelta(days=(today.weekday() - WEEKDAYS[name]) % 7)
    return None


ALL_SHOPS: list[dict[str, Any]] = []  # tutti i locali di locali.json (impostato da run)
# nomi di locale che sono anche parole comuni ("fantasia di verdure"): per questi conta solo il telefono
COMMON_NAME_WORDS = {"fantasia", "impasta"}


def plain(text: str) -> str:
    """Minuscolo e senza accenti: "IMPASTAMÒ" -> "impastamo"."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text or "").lower() if not unicodedata.combining(c))


def other_shop_named(text: str, shop: dict[str, Any]) -> str:
    """Nome dell'ALTRO locale a cui appartiene il menu letto, se nel testo (post o foto) compare
    il suo telefono o il suo nome e non quello del locale controllato. Es. Le delizie di Michela
    che ricondivide nella sua storia il menu di Impastamò ("IMPASTAMÒ ... 3925361536")."""
    flat, digits = plain(text), re.sub(r"\D", "", text or "")
    own = plain(shop.get("nome", ""))
    own_phone = re.sub(r"\D", "", shop.get("telefono", ""))
    if (own and re.search(rf"\b{re.escape(own)}\b", flat)) or (len(own_phone) >= 8 and own_phone in digits):
        return ""
    for other in ALL_SHOPS:
        if other.get("id") == shop.get("id"):
            continue
        phone = re.sub(r"\D", "", other.get("telefono", ""))
        name = plain(re.sub(r"\s*\(.*?\)", "", other.get("nome", ""))).strip()
        if len(phone) >= 8 and phone in digits:
            return other["nome"]
        if name and name not in COMMON_NAME_WORDS and len(name) >= 5 and re.search(rf"\b{re.escape(name)}\b", flat):
            return other["nome"]
    return ""


class NotAMenu(RuntimeError):
    """Il post letto non è un menu (pubblicità, foto di un piatto, auguri...)."""


DISCARDED = DATA / "scartati"  # foto tolte da ingresso perché non erano il menu di quel giorno
CAPTURES = DATA / "catture"  # foto appena scaricate, prima di sapere se sono un menu


def place_capture(folder: Path, image: Path) -> Path:
    """Sposta una foto da dati/catture in ingresso/<id> come AAAA-MM-GG_online.jpg (menu di oggi)."""
    if image.parent == folder:
        return image
    final = folder / f"{date.today().isoformat()}_online{image.suffix}"
    image.replace(final)
    return final


def post_variants(source: dict[str, Any]) -> list[dict[str, Any]]:
    """La fonte per il 1°, 2°, 3°... post della pagina ("post_da_controllare", 5 se non indicato):
    nei selettori [aria-posinset='1'] diventa '2', '3'..."""
    count = int(source.get("post_da_controllare", 5))
    if "posinset='1'" not in source.get("selettore", ""):
        return [source]
    return [{**source, **{key: source[key].replace("posinset='1'", f"posinset='{number}'")
                          for key in ("selettore", "selettore_testo") if source.get(key)}}
            for number in range(1, count + 1)]


def same_picture(first: Path, second: Path) -> bool:
    """Stessa foto? Identica byte per byte, oppure uguale a occhio (Facebook può dare la stessa
    foto ricompressa o in un'altra misura): confronto le due immagini ridotte a 16x16 in grigi."""
    try:
        if file_hash(first) == file_hash(second):
            return True
        from PIL import Image
        def tiny(path: Path) -> list[int]:
            with Image.open(path) as picture:
                return list(picture.convert("L").resize((16, 16)).getdata())
        a, b = tiny(first), tiny(second)
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a) < 6
    except Exception:
        return False


def discard_wrong_menu(shop: dict[str, Any], folder: Path, image: Path) -> None:
    """Il post appena letto NON è un menu. Se la stessa foto era stata salvata in ingresso come
    menu (es. con un controllo meno severo, o prima di una correzione), era un errore: la sposto
    in dati/scartati, così né la pagina né il Monitor la mostrano più come menu di quel giorno.
    Poi elimino la foto appena scaricata."""
    try:
        for saved in folder.glob("*_online.*"):
            if saved.suffix.lower() in IMAGE_EXTENSIONS and same_picture(saved, image):
                DISCARDED.mkdir(parents=True, exist_ok=True)
                saved.replace(DISCARDED / f"{shop['id']}_{saved.name}")
                print(f"  {shop['nome']} - tolto {saved.name}: era questa foto, che non è un menu (ora in dati/scartati)")
    finally:
        image.unlink(missing_ok=True)


def browser_result(shop: dict[str, Any], source: dict[str, Any], folder: Path, browser: "BrowserCollector",
                   checked: str) -> Result:
    """Un post Facebook/Instagram (fonte "browser"): menu, avviso o NotAMenu se non è un menu.
    La foto viene scaricata in dati/catture e spostata in ingresso solo se viene usata, così
    un post che non è un menu non cancella mai il menu già salvato oggi."""
    if browser is None:
        raise RuntimeError("browser non disponibile")
    image = browser.capture(shop, source)
    if image is None:  # post solo testo
        return text_post_result(shop, source, folder, browser.last_text, checked, browser.last_posted)
    photo_text = ocr_image(image)  # testo scritto nella foto (es. avviso di chiusura)
    seen = " ".join(f"{browser.last_text} {photo_text or browser.last_alt}".split())
    if seen:
        print(f"  {shop['nome']} - testo letto: {seen[:160]}")  # utile nel registro
    owner = other_shop_named(f"{browser.last_text}\n{photo_text}", shop)
    if owner:
        discard_wrong_menu(shop, folder, image)
        raise NotAMenu(f"è il menu di {owner}")
    learn_rest_days(shop, f"{browser.last_text}\n{photo_text}")
    closed = closure_period(f"{browser.last_text}\n{browser.last_alt}\n{photo_text}")
    if closed:
        print(f"  {shop['nome']} - avviso di chiusura dal {closed[0]:%d/%m} al {closed[1]:%d/%m}")
    if closed and closed[0] <= date.today() <= closed[1]:
        # avviso di chiusura valido oggi: lo pubblico come "menu del giorno"
        image, _ = keep_first_seen(folder, image)
        if image.suffix.lower() in IMAGE_EXTENSIONS and closure_today(photo_text):
            return Result(save_closure(folder, closed, image), date.today(), source.get("nome", "browser"), checked)
        return Result(save_closure(folder, closed, None, browser.last_text), date.today(),
                      source.get("nome", "browser"), checked)
    # l'ultimo post è davvero un menu? (non una pubblicità, una foto di un piatto, ecc.)
    words = source.get("parole_menu", MENU_WORDS)
    if words and not any(word in f"{browser.last_text} {photo_text}".lower() for word in words):
        discard_wrong_menu(shop, folder, image)
        raise NotAMenu("il post non sembra un menu")
    if not photo_has_writing(photo_text, browser.last_alt) and not text_lists_menu(browser.last_text, source):
        # foto SENZA scritte (es. una teglia di pasta): il menu allora deve essere scritto nel
        # post, con le portate (primi, secondi...) o la data. Una frase come "il menù del giorno
        # te lo portiamo a casa" (pubblicità "Pranzo Pass" di Impasta) non basta.
        discard_wrong_menu(shop, folder, image)
        raise NotAMenu("foto senza scritte e post senza un menu scritto")
    readable = len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", photo_text or "")) >= 6
    if not text_is_menu(f"{browser.last_text}\n{photo_text}", source) and (
            readable or (not photo_has_writing(photo_text, browser.last_alt)
                         and not text_is_menu(browser.last_text, source))):
        # né il testo del post né le scritte della foto dicono "menu del giorno", sezioni
        # (primi, secondi...) o una data: es. la foto pubblicitaria del "Pranzo Pass" con
        # "menù" nel testo del post. Se la foto è scritta a mano e non si legge (es. Fantasia)
        # vale la regola di prima: foto con scritte e "menu" nel testo del post.
        discard_wrong_menu(shop, folder, image)
        raise NotAMenu("né il post né la foto sembrano un menu")
    text_day = plausible_menu_date(browser.last_text) or plausible_menu_date(photo_text)
    if text_day:
        # data scritta nel post (es. "menù del giorno 4 Ottobre"): rinomino il file con quella data
        dated = folder / f"{text_day.isoformat()}_online{image.suffix}"
        if dated != image:
            image.replace(dated)
        return Result(dated, text_day, source.get("nome", "browser"), checked)
    posted = browser.last_posted
    if posted and posted < date.today():
        # post pubblicato in un giorno precedente (es. ieri sera, letto dopo mezzanotte):
        # vale per quel giorno, non per oggi
        print(f"  {shop['nome']} - post pubblicato il {posted:%d/%m}: non è il menu di oggi")
        # i post sono dal più recente: se l'ultimo menu è di un giorno precedente, una foto
        # salvata come "menu di oggi" in un giro precedente era sbagliata (es. foto pubblicitaria
        # letta dopo mezzanotte): la tolgo da ingresso (resta in dati/scartati, non si perde)
        for stale in folder.glob(f"{date.today().isoformat()}_online.*"):
            DISCARDED.mkdir(parents=True, exist_ok=True)
            stale.replace(DISCARDED / f"{shop['id']}_{stale.name}")
            print(f"  {shop['nome']} - tolto {stale.name} (non era il menu di oggi): ora in dati/scartati")
        dated = folder / f"{posted.isoformat()}_online{image.suffix}"
        image.replace(dated)
        return Result(dated, posted, source.get("nome", "browser"), checked)
    if source.get("data") == "novita":
        image, new_day = keep_first_seen(folder, image)
        return Result(place_capture(folder, image), new_day, source.get("nome", "browser"), checked)
    return Result(place_capture(folder, image), date.today(), source.get("nome", "browser"), checked)


FACEBOOK_LOGIN: dict[str, Any] = {}  # profilo Chrome collegato a Facebook (impostato da run)


def with_facebook_login(source: dict[str, Any]) -> dict[str, Any]:
    """Le pagine Facebook vanno lette con il profilo Chrome collegato a Facebook (quello delle
    storie, es. Le delizie di Michela). Senza accesso Facebook mostra una selezione di post vecchi
    e spesso NON il menu appena pubblicato: per questo ogni fonte facebook.com senza un suo
    "profilo" usa quello collegato. Per tornare al profilo scollegato: "profilo": "profilo"."""
    if FACEBOOK_LOGIN and "facebook.com" in source.get("url", "") and not source.get("profilo"):
        return {**FACEBOOK_LOGIN, **source}
    return source


def find_facebook_login(shops: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    """Profilo collegato a Facebook: impostazioni > "profilo_facebook" oppure quello della prima
    fonte facebook.com che ne indica uno (oggi la storia di Le delizie di Michela)."""
    if settings.get("profilo_facebook"):
        return {"profilo": settings["profilo_facebook"], "canale": settings.get("canale_facebook", "chrome"),
                "fuori_schermo": True}
    for shop in shops:
        for source in shop.get("fonti", []):
            if "facebook.com" in source.get("url", "") and source.get("profilo"):
                return {key: source[key] for key in ("profilo", "canale", "fuori_schermo") if key in source}
    return {}


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


PUBLISHED_AT = DATA / "pubblicazione.json"  # ora dell'ultimo aggiornamento mostrata nel titolo
REPORTED = DATA / "segnalati.json"  # impronte delle foto/testi segnalati come sbagliati, per locale
SUSPECT: set[str] = set()  # locali segnalati in questo giro (clic sul nome nel Monitor)


def reported_hashes(shop: dict[str, Any]) -> set[str]:
    return set(read_json(REPORTED, {}).get(shop["id"], []))


def is_reported(shop: dict[str, Any], path: Path | None) -> bool:
    """La foto (o il testo) è identica a una segnalata come sbagliata per questo locale?"""
    return bool(path and path.is_file() and file_hash(path) in reported_hashes(shop))


def report_wrong(shop: dict[str, Any], folder: Path) -> None:
    """Segnalazione dal Monitor (clic sul nome del locale): il menu di oggi è probabilmente sbagliato.
    Le foto/testi di oggi del locale vanno in dati/scartati e la loro impronta viene ricordata in
    dati/segnalati.json: quella stessa foto non verrà più accettata per quel locale."""
    today = date.today().isoformat()
    marks = read_json(REPORTED, {})
    known = set(marks.get(shop["id"], []))
    candidates = [item for item in folder.glob(f"{today}*") if item.is_file() and not item.stem.endswith("_riposo")]
    candidates += [item for item in CURRENT.glob(f"{shop['id']}.*") if item.is_file()]
    for item in candidates:
        known.add(file_hash(item))
        if item.parent == folder:
            DISCARDED.mkdir(parents=True, exist_ok=True)
            item.replace(DISCARDED / f"{shop['id']}_segnalato_{item.name}")
            print(f"  {shop['nome']} - SEGNALATO: tolto {item.name} (ora in dati/scartati), non verrà più usato")
    marks[shop["id"]] = sorted(known)
    save_json(REPORTED, marks)
    if not candidates:
        print(f"  {shop['nome']} - SEGNALATO: nessun menu di oggi da togliere, controllo approfondito")


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
    for leftover in folder.glob("*_avviso_controllo.*"):
        # foto di servizio rimasta in ingresso da un giro interrotto (versioni vecchie): via
        DISCARDED.mkdir(parents=True, exist_ok=True)
        leftover.replace(DISCARDED / f"{shop['id']}_{leftover.name}")
        print(f"  {shop['nome']} - tolto {leftover.name} (foto di servizio, non è un menu)")
    if shop["id"] in SUSPECT:
        report_wrong(shop, folder)
        skip_if_today = None  # controllo approfondito: si ricontrolla tutto online
    if date.today().weekday() in rest_days(shop):
        return rest_day_result(shop, folder, checked)
    if online and skip_if_today is not None:
        latest = newest_image(folder)
        if latest and latest.stem.endswith("_storia") and not image_is_sharp(latest.read_bytes()):
            latest.unlink()  # anteprima sfocata di una storia: va ricatturata
            latest = newest_image(folder)
        # un menu solo testo viene comunque riletto: il post può essere stato completato o corretto
        if latest and image_date(latest) == date.today() and not latest.stem.endswith("_testo") \
                and not is_reported(shop, latest):
            return Result(latest, date.today(), skip_if_today, checked)
    if online:
        notice = check_notice(shop, browser, checked)
        if notice:
            return notice
    result = acquire_sources(shop, browser, online, folder, checked)
    skipped: set[str] = set()
    while result.image and is_reported(shop, result.image) and len(skipped) < 6:
        # è la stessa foto/testo segnalata come sbagliata: non si usa, si riprova senza quella fonte
        print(f"  {shop['nome']} - {result.image.name} era stato segnalato come sbagliato: non lo uso")
        if result.image.parent == folder:
            DISCARDED.mkdir(parents=True, exist_ok=True)
            result.image.replace(DISCARDED / f"{shop['id']}_segnalato_{result.image.name}")
        skipped.add(result.source)
        result = acquire_sources(shop, browser, online, folder, checked, skipped)
    # anche una foto messa a mano in ingresso o scaricata può essere un avviso di chiusura
    image = result.image
    if image and image.suffix.lower() in IMAGE_EXTENSIONS and "_chiusura_fino_" not in image.name:
        closed = closure_today(ocr_image(image))
        if closed:
            print(f"  {shop['nome']} - avviso di chiusura (foto) dal {closed[0]:%d/%m} al {closed[1]:%d/%m}")
            return Result(save_closure(folder, closed, image), date.today(), result.source, checked)
    return result


def acquire_sources(shop: dict[str, Any], browser: BrowserCollector | None, online: bool,
                    folder: Path, checked: str, skipped: set[str] | None = None) -> Result:
    """Prova le fonti del locale nell'ordine indicato in locali.json (saltando quelle in skipped,
    che hanno appena dato una foto segnalata come sbagliata)."""
    errors: list[str] = []
    for source in shop.get("fonti", [{"tipo": "cartella"}]):
        if not source.get("attiva", True):
            continue
        source = with_facebook_login(source)
        kind = source.get("tipo", "cartella")
        if skipped and kind != "cartella" and source.get("nome", kind) in skipped:
            continue
        if shop["id"] in SUSPECT and kind == "browser":
            # controllo approfondito: si guardano più post del solito
            source = {**source, "post_da_controllare": max(10, int(source.get("post_da_controllare", 5)))}
        try:
            if kind == "cartella":
                image = newest_image(folder)
                # foto di oggi salvata da una versione precedente ma che è il menu di un ALTRO
                # locale (es. storia di Michela con il menu di Impastamò): via in dati/scartati
                while image and is_reported(shop, image):
                    DISCARDED.mkdir(parents=True, exist_ok=True)
                    image.replace(DISCARDED / f"{shop['id']}_segnalato_{image.name}")
                    print(f"  {shop['nome']} - tolto {image.name}: era stato segnalato come sbagliato")
                    image = newest_image(folder)
                while image and image_date(image) == date.today() and image.suffix.lower() in IMAGE_EXTENSIONS:
                    owner = other_shop_named(ocr_image(image), shop)
                    if not owner:
                        break
                    DISCARDED.mkdir(parents=True, exist_ok=True)
                    image.replace(DISCARDED / f"{shop['id']}_{image.name}")
                    print(f"  {shop['nome']} - tolto {image.name}: era il menu di {owner} (ora in dati/scartati)")
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
                owner = other_shop_named(ocr_image(image), shop)
                if owner:
                    # la storia mostrava il menu di un altro locale (ricondiviso, o Facebook è passato
                    # da solo alla storia successiva): non è il menu di questo locale
                    DISCARDED.mkdir(parents=True, exist_ok=True)
                    image.replace(DISCARDED / f"{shop['id']}_{image.name}")
                    print(f"  {shop['nome']} - la storia mostra il menu di {owner}: non la uso (ora in dati/scartati)")
                    raise RuntimeError(f"la storia mostra il menu di {owner}")
                image, new_day = keep_first_seen(folder, image)  # stessa foto di ieri = non è il menu di oggi
                return Result(image, new_day, source.get("nome", kind), checked)
            elif kind == "browser":
                if browser is None:
                    raise RuntimeError("browser non disponibile")
                # se il primo post non è un menu (pubblicità, foto, post in evidenza...) guarda i successivi
                # e anche se un post non si riesce a leggere (video, reel, post senza testo né foto,
                # post caricato in ritardo): un post illeggibile non deve fermare la ricerca
                tries = post_variants(source)
                for number, variant in enumerate(tries, 1):
                    try:
                        return browser_result(shop, variant, folder, browser, checked)
                    except NotAMenu:
                        if number == len(tries):
                            raise
                        print(f"  {shop['nome']} - post {number} non è un menu: guardo il successivo")
                    except Exception as exc:
                        if number == len(tries) or number >= 2 and "nessun elemento" in str(exc):
                            raise  # finiti i post (o la pagina non ne carica altri)
                        print(f"  {shop['nome']} - post {number} non leggibile ({str(exc)[:70]}): guardo il successivo")
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
    admin = {**manifest, "start_url": "./admin.html", "id": "./admin.html"}
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
        town = shop_town(shop)
        town_html = f'\n  <span class="town">{html.escape(town)}</span>' if town else ""
        cards.append(f'''<button type="button" class="card {'band-ok' if css == 'fresh' else 'band-old'}" data-index="{index}">
  {logo_html}<span class="info">
  <h2>{html.escape(shop['nome'])}</h2>
  <span class="day"><strong>{day_text}</strong></span>{note_html}
  <span class="status {css}">{label}</span>
  </span>
  <span class="count" hidden></span>{town_html}
</button>''')
    public_shops = [{**{key: shop.get(key, "") for key in ("nome", "telefono", "indirizzo", "url")},
                     # nomi usati prima di un cambio di nome: i loro click restano sommati al locale
                     # indirizzo da mostrare nella scheda: "Via ... numero - Comune"
                     "indirizzo_breve": short_address(shop),
                     "comune": shop_town(shop),
                     "nomi_precedenti": shop.get("nomi_precedenti", [])} for shop in shops]
    # niente orari di controllo nella pagina: se i menu non cambiano la pagina resta identica
    # e la pubblicazione automatica non crea un nuovo invio ogni 15 minuti
    public_results = [{key: value for key, value in result.items() if key != "checked_at"} for result in results]
    # versione di ogni foto = impronta del suo contenuto: se il menu cambia ma il file ha lo stesso
    # nome (es. menu/eclettico.jpg riscaricato più pulito), l'indirizzo cambia e browser e
    # telefoni non mostrano la copia vecchia rimasta in memoria
    for item in public_results:
        picture = ROOT / item["image"] if item.get("image") else None
        if picture and picture.is_file():
            item["v"] = file_hash(picture)[:10]
    version = hashlib.sha1(json.dumps(public_results, sort_keys=True).encode()).hexdigest()[:10]
    # ora dell'ultimo aggiornamento pubblicato ("Menu - Venerdì 9 ottobre - 09:43"): cambia solo
    # quando cambia il contenuto, così un giro senza novità non crea una nuova pubblicazione
    stamp = read_json(PUBLISHED_AT, {})
    if stamp.get("v") != version or stamp.get("data") != date.today().isoformat() or not stamp.get("ora"):
        stamp = {"v": version, "data": date.today().isoformat(), "ora": datetime.now().strftime("%H:%M")}
        save_json(PUBLISHED_AT, stamp)
    title_tpl = settings.get("titolo", "Menu - {data}")
    payload = json.dumps({"shops": public_shops, "results": public_results, "v": version, "titolo": title_tpl,
                          "ora": stamp["ora"],
                          "log": settings.get("registro_click_url", ""),
                          # visite alla pagina (foglio "Visite"): solo dopo aver aggiornato Registro_click.gs
                          "visite": bool(settings.get("registra_visite", False))}, ensure_ascii=False).replace("</", "<\\/")
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
<script>/* ?v=57 = amministratore: si passa a admin.html (stessa pagina), il cui indirizzo resta anche
   nell'app aggiunta alla Home; lì il manifest "amministratore" fa riaprire l'app sempre su admin.html */
(function(){{const adm=location.pathname.endsWith('/admin.html');
if(!adm&&new URLSearchParams(location.search).get('v')==='57'){{location.replace('admin.html');return}}
document.write('<link rel="manifest" href="'+(adm?'manifest-admin':'manifest')+'.webmanifest?v={iv}">')}})()</script><link rel="icon" type="image/png" href="icone/favicon.png?v={iv}">
<link rel="apple-touch-icon" sizes="180x180" href="icone/icona-180.png?v={iv}"><meta name="apple-mobile-web-app-title" content="Menu">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
:root{{--bg:#0b1220;--card:#fff;--ink:#172033;--muted:#64748b;--accent:#16a34a}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(150deg,#09111f,#172033);font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:white;min-height:100vh}}
header{{max-width:1500px;margin:auto;padding:max(28px,calc(env(safe-area-inset-top) + 12px)) 20px 18px;display:flex;justify-content:flex-start;align-items:end;gap:20px}}h1{{margin:0;font-size:clamp(28px,4vw,46px);cursor:pointer}}h1 .sub{{font-size:.5em}}header p{{margin:5px 0 0;color:#cbd5e1}}.updated{{font-size:13px;color:#94a3b8}}
main{{max-width:1500px;margin:auto;padding:12px 20px 40px;display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:18px}}
.card{{display:flex;flex-direction:column;align-items:flex-start;gap:8px;text-align:left;width:100%;background:var(--card);color:var(--ink);border-radius:16px;padding:18px 20px;box-shadow:0 10px 28px #0005;cursor:pointer;transition:.18s transform,.18s box-shadow;font:inherit;position:relative}}
.card{{flex-direction:row;align-items:center;gap:16px}}.info{{display:flex;flex-direction:column;align-items:flex-start;gap:8px;min-width:0;flex:1}}.logo{{width:64px;height:64px;flex:none;border-radius:14px;object-fit:cover;background:#fff;box-shadow:0 2px 8px #0003}}
.count{{position:absolute;right:14px;bottom:12px;min-width:28px;padding:2px 9px;border-radius:999px;background:#1e293b;color:#fff;font-size:14px;font-weight:800;text-align:center}}
.infocard{{display:flex;flex-direction:row;align-items:center;gap:16px;background:#d1d5db;color:#172033;border:4px solid #facc15;border-radius:16px;padding:18px 20px 18px 26px;box-shadow:0 10px 28px #0005;text-align:left}}
.infocard h2{{margin:0;font-size:21px}}.infocard .total{{width:64px;height:64px;flex:none;display:flex;align-items:center;justify-content:center;border-radius:14px;background:#fff;box-shadow:0 2px 8px #0003;font-size:32px;font-weight:800;color:#172033}}.infocard small{{color:#475569;font-size:14px}}.infocard{{cursor:pointer}}.infocard:hover,.infocard:focus-visible{{transform:translateY(-3px);outline:3px solid #facc15}}
#stats{{width:min(460px,96vw);box-sizing:border-box;border:5mm solid #38bdf8}}.modal-head{{background:#3f3f46;border-bottom-color:#52525b}}#stats .x{{background:#52525b;color:white;font-size:18px}}.statwrap{{padding:10px 14px 16px;overflow:auto}}#stats table{{width:100%;border-collapse:collapse;font-size:16px}}#stats th,#stats td{{padding:3px 8px;line-height:1.25;white-space:nowrap;border-bottom:1px solid #1e293b;text-align:right}}#stats th:first-child,#stats td:first-child{{text-align:left}}#stats th{{color:#facc15;font-size:14px;cursor:pointer;user-select:none;white-space:nowrap}}#stats th::after{{content:attr(data-dir);font-size:10px;margin-left:3px}}#stats tfoot td{{font-weight:800;border-top:2px solid #facc15;border-bottom:0}}.statnote{{margin:10px 0 0;color:#94a3b8;font-size:13px}}.card:hover,.card:focus-visible{{transform:translateY(-3px);box-shadow:0 14px 32px #0008;outline:3px solid var(--accent)}}
.band-ok{{border-left:10px solid #16a34a;background:#dcfce7}}#detail{{width:100vw;height:100vh;height:100dvh;max-width:none;max-height:none;margin:0;inset:0;padding:0;border:0;border-radius:0;background:transparent;box-shadow:none;overflow:hidden}}
#detail[open]{{display:flex;align-items:center;justify-content:center}}
#detail .sheet{{box-sizing:border-box;width:min(920px,96vw);max-height:94vh;max-height:94dvh;overflow:auto;-webkit-overflow-scrolling:touch;border-radius:18px;background:#050a12;color:white;box-shadow:0 24px 70px #000b;border:5mm solid #f97316}}
#detail.ok .sheet{{border-color:#16a34a}}#detail.old .sheet{{border-color:#f97316}}.band-old{{border-left:10px solid #f97316;background:#fef3c7}}
.card h2{{font-size:21px;margin:0}}.day{{color:var(--muted);font-size:15px}}.day strong{{color:var(--ink)}}.note{{color:#b45309;font-size:14px;font-weight:700}}.card .status,.card .day{{display:none}}#name .mdate{{font-size:.65em}}.sheet{{display:flex;flex-direction:column;height:94vh;height:94dvh;overflow:hidden}}.modal-head,.actions,#nomenu{{flex:none}}.modal-head>div{{min-width:0}}#name,#addr{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}#full{{flex:1 1 0;min-height:0;width:100%;height:auto;max-height:none;object-fit:contain}}#text{{flex:1 1 0;min-height:0;max-height:none}}#text:empty{{display:none}}.actions{{margin-top:auto}}.town{{position:absolute;top:8px;right:14px;font-size:12px;font-weight:400;color:#000;line-height:1}}.status{{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:750}}.fresh{{background:#dcfce7;color:#166534}}.stale{{background:#fef3c7;color:#92400e}}.missing{{background:#fee2e2;color:#991b1b}}
#text{{max-height:68vh;overflow:auto;padding:0 18px}}#text h3{{margin:18px 0 8px;color:#86efac}}.dish{{display:grid;grid-template-columns:1fr auto;gap:2px 12px;padding:8px 0;border-bottom:1px solid #1e293b}}.dish b{{white-space:nowrap}}.dish small{{grid-column:1/-1;color:#94a3b8}}.dish small:empty{{display:none}}
#nomenu{{padding:40px 18px;text-align:center;color:#cbd5e1}}
dialog{{width:min(920px,96vw);max-height:94vh;padding:0;border:0;border-radius:18px;background:#050a12;color:white;box-shadow:0 24px 70px #000b}}dialog::backdrop{{background:#000c}}.modal-head{{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #334155}}.nav{{position:absolute;top:50%;transform:translateY(-50%);z-index:5;width:52px;height:52px;padding:0;border-radius:50%;background:rgba(255,255,255,.1);color:#fff;font-size:34px;line-height:48px;box-shadow:0 6px 18px #0008;text-shadow:0 1px 4px #000c}}.nav:hover,.nav:focus-visible{{background:rgba(255,255,255,.25)}}#prev{{left:max(6px,calc(50vw - min(460px,48vw) - 66px))}}#next{{right:max(6px,calc(50vw - min(460px,48vw) - 66px))}}
.modal-head h2{{margin:0}}#addr{{margin:4px 0 0;font-size:15px;font-weight:400;color:#cbd5e1}}button{{border:0;border-radius:10px;padding:10px 14px;font-weight:700;cursor:pointer}}#close{{background:#52525b;color:white;font-size:18px}}#full{{display:block;max-width:100%;max-height:68vh;margin:auto;object-fit:contain}}#full[hidden]{{display:none}}.actions{{padding:14px 18px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:center}}.actions a{{color:white;text-decoration:none;background:#166534;padding:10px 14px;border-radius:10px;font-weight:700}}.actions .source{{background:#1d4ed8}}#meta{{color:#cbd5e1;flex-basis:100%;text-align:center}}#meta:empty{{display:none}}footer{{text-align:center;color:#94a3b8;padding:0 20px 28px;font-size:13px}}
#home{{flex:none;display:flex;align-items:center;justify-content:center;padding:0;border:0;width:52px;height:52px;border-radius:14px;background:#334155;color:#fff;font-size:28px;line-height:1;cursor:pointer;box-shadow:0 6px 18px #0006}}.towncard{{display:flex;flex-direction:row;flex-wrap:wrap;align-items:baseline;gap:4px 8px;width:100%;text-align:left;font:inherit;background:var(--card);color:var(--ink);border:0;border-left:10px solid #16a34a;border-radius:16px;padding:14px 22px;box-shadow:0 10px 28px #0005;cursor:pointer}}.towncard.all{{border-left-color:#16a34a}}.towncard.none{{border-left-color:#f97316}}.infocard{{padding:3px 20px 3px 26px}}.infocard .total{{width:40px;height:40px;font-size:20px;border-radius:10px}}.towncard{{flex-wrap:nowrap;white-space:nowrap}}.towncard b{{font-size:25px}}.towncard small{{flex:none}}.towncard{{background:#e0f2fe}}.towncard .tok{{color:#ea580c}}.towncard .ttot{{color:#15803d}}.towncard .tok.full{{color:#15803d}}.towncard small{{font-size:15px;color:#334155}}.card[hidden],.towncard[hidden],.infocard[hidden],#home[hidden]{{display:none!important}}@media(max-width:600px){{header{{align-items:center}}h1{{font-size:clamp(22px,7vw,30px)}}#home{{width:46px;height:46px;font-size:24px}}main{{grid-template-columns:1fr;padding-inline:12px}}}}
</style></head><body>
<header><h1>{title}</h1><button id="home" hidden aria-label="Torna alla scelta del Comune" title="Torna alla scelta del Comune">🏠</button></header>
<main>{''.join(cards)}</main>
<dialog id="detail"><div class="sheet"><div class="modal-head"><div><h2 id="name"></h2><p id="addr" hidden></p></div><button id="close" aria-label="Chiudi">✕</button></div><img id="full" alt=""><div id="text"></div><p id="nomenu" hidden>Il menu di questa data non è pubblicato (vedi archivio).</p><div class="actions"><span id="meta"></span><a id="phone" hidden></a><a id="map" target="_blank" rel="noopener" hidden>Google Maps</a><a id="source" class="source" target="_blank" rel="noopener" hidden>Fonte</a></div></div><button class="nav" id="prev" aria-label="Rosticceria precedente">‹</button><button class="nav" id="next" aria-label="Rosticceria successiva">›</button></dialog>
<script>const DATA={payload};
/* Registro dei click sul foglio Google "Menu" (Apps Script in Registro_click.gs).
   ?v=57 nell'indirizzo = amministratore: quei click non vengono registrati. Senza ?v=57 si registra
   sempre, anche sullo stesso dispositivo (non viene ricordato nulla). */
const Q=new URLSearchParams(location.search);const ADMIN=Q.get('v')==='57'||location.pathname.endsWith('/admin.html');
try{{localStorage.removeItem('menuAdmin')}}catch(e){{}}
function deviceLabel(){{const u=navigator.userAgent||'';let o='Altro',b='Altro';
if(/iPad/.test(u))o='iPad';else if(/iPhone/.test(u))o='iPhone';else if(/Android/.test(u))o='Android';else if(/Macintosh/.test(u))o='Mac';else if(/Windows/.test(u))o='Windows';else if(/Linux/.test(u))o='Linux';
if(/Edg\\//.test(u))b='Edge';else if(/OPR\\//.test(u))b='Opera';else if(/CriOS\\//.test(u)||/Chrome\\//.test(u))b='Chrome';else if(/FxiOS\\//.test(u)||/Firefox\\//.test(u))b='Firefox';else if(/Safari\\//.test(u))b='Safari';return o+' / '+b}}
let placeP=null;function approxPlace(){{if(!placeP)placeP=fetch('https://ipwho.is/',{{cache:'no-store'}}).then(r=>r.json()).then(d=>d&&d.success!==false?[d.city,d.region,d.country_code].filter(Boolean).join(', '):'').catch(()=>'');return placeP}}
if(!ADMIN&&DATA.log&&!navigator.webdriver){{approxPlace();/* visita alla pagina: una per apertura del sito, anche senza aprire schede (foglio "Visite") */if(DATA.visite)Promise.race([approxPlace(),new Promise(r=>setTimeout(()=>r(''),1500))]).then(p=>sendLog(JSON.stringify({{tipo:'visita',pagina:decodeURIComponent(location.hash.slice(1))||'iniziale',dispositivo:deviceLabel(),posizione:p||''}})))}}
function sendLog(body){{try{{if(navigator.sendBeacon&&navigator.sendBeacon(DATA.log,new Blob([body],{{type:'text/plain'}})))return}}catch(e){{}}try{{fetch(DATA.log,{{method:'POST',mode:'no-cors',keepalive:true,body}})}}catch(e){{}}}}
function logClick(name){{if(ADMIN||!DATA.log||navigator.webdriver)return;/* navigator.webdriver: pagina aperta da un programma (Panorama, robot): non è una visita vera */Promise.race([approxPlace(),new Promise(r=>setTimeout(()=>r(''),1500))]).then(p=>sendLog(JSON.stringify({{rosticceria:name,dispositivo:deviceLabel(),posizione:p||''}})))}}const dlg=document.querySelector('#detail');let cur=0;function openCard(i,dir){{cur=i;const s=DATA.shops[i],r=DATA.results[i];const go=()=>{{
/* cornice doppia della scheda: verde se il menu di oggi è pubblicato, arancione se no (stessi colori delle schede) */
const n0=new Date(),iso0=n0.getFullYear()+'-'+String(n0.getMonth()+1).padStart(2,'0')+'-'+String(n0.getDate()).padStart(2,'0');dlg.classList.toggle('ok',r.menu_date===iso0);dlg.classList.toggle('old',r.menu_date!==iso0);logClick(s.nome);const nm=document.querySelector('#name');nm.textContent=s.nome+(r.menu_date?' - ':'');if(r.menu_date){{const md=document.createElement('span');md.className='mdate';const dn=document.createElement('span');dn.className='dnum';dn.textContent=(+r.menu_date.slice(8,10));nm.append(dn);md.textContent=' '+['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto','settembre','ottobre','novembre','dicembre'][+r.menu_date.slice(5,7)-1];nm.append(md)}}const ad=document.querySelector('#addr');ad.textContent=s.indirizzo_breve||s.indirizzo||'';ad.hidden=!(s.indirizzo_breve||s.indirizzo);const img=document.querySelector('#full');img.src=r.image?r.image+'?v='+(r.v||DATA.v):'';img.hidden=!r.image;const tx=document.querySelector('#text');tx.innerHTML='';(r.sections||[]).forEach(sec=>{{const h=document.createElement('h3');h.textContent=sec.titolo;tx.append(h);sec.piatti.forEach(p=>{{const d=document.createElement('div');d.className='dish';d.innerHTML='<span></span><b></b><small></small>';d.children[0].textContent=p.nome;d.children[1].textContent=p.prezzo;d.children[2].textContent=p.descrizione;tx.append(d)}})}});document.querySelector('#nomenu').hidden=!!(r.image||(r.sections||[]).length);document.querySelector('#meta').textContent=r.nota||(r.menu_date?'':'Menu non disponibile');const phone=document.querySelector('#phone');phone.hidden=!s.telefono;phone.textContent=s.telefono||'';phone.href='tel:'+(s.telefono||'').replace(/[^+\\d]/g,'');const map=document.querySelector('#map');map.hidden=!s.indirizzo;map.href='https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(s.nome.replace(/\\s*\\(.*\\)/,'')+', '+(s.indirizzo||''));const source=document.querySelector('#source');source.hidden=!s.url;source.href=s.url||'';if(!dlg.open)dlg.showModal();dlg.querySelector('.sheet').scrollTop=0;
if(dir)['#full','#text','#nomenu'].forEach(q=>{{const e=document.querySelector(q);if(e&&e.animate)e.animate([{{opacity:.4}},{{opacity:1}}],{{duration:180,easing:'ease-out'}})}})}};if(dlg.open&&r.image){{const p=new Image();let done=false;const f=()=>{{if(!done&&cur===i){{done=true;go()}}}};p.onload=p.onerror=f;setTimeout(f,1500);p.src=r.image+'?v='+(r.v||DATA.v)}}else go();}}
/* scheda precedente / successiva in modo circolare: frecce ai lati, tasti ← → e, sul telefono,
   scorrimento del dito a destra o a sinistra */
function step(d){{const order=[...document.querySelectorAll('.card')].filter(c=>!c.hidden).map(c=>+c.dataset.index),n=order.length;openCard(order[(order.indexOf(cur)+d+n)%n],d)}}
document.querySelector('#prev').onclick=e=>{{e.stopPropagation();step(-1)}};document.querySelector('#next').onclick=e=>{{e.stopPropagation();step(1)}};
document.addEventListener('keydown',e=>{{if(!dlg.open)return;if(e.key==='ArrowLeft'){{e.preventDefault();step(-1)}}else if(e.key==='ArrowRight'){{e.preventDefault();step(1)}}}});
let tx=null,ty=0;dlg.addEventListener('touchstart',e=>{{if(e.touches.length!==1){{tx=null;return}}tx=e.touches[0].clientX;ty=e.touches[0].clientY}},{{passive:true}});
dlg.addEventListener('touchmove',e=>{{if(e.touches.length!==1)tx=null}},{{passive:true}});
dlg.addEventListener('touchend',e=>{{if(tx===null)return;const t=e.changedTouches[0],dx=t.clientX-tx,dy=t.clientY-ty;tx=null;
if(Math.abs(dx)>50&&Math.abs(dx)>1.5*Math.abs(dy)&&!(window.visualViewport&&visualViewport.scale>1.05))step(dx<0?1:-1)}});document.querySelectorAll('.card').forEach(c=>{{c.onclick=()=>openCard(+c.dataset.index)}});document.querySelector('#close').onclick=()=>dlg.close();dlg.onclick=e=>{{if(e.target===dlg)dlg.close()}};/* Navigazione per Comune: la pagina iniziale mostra un riquadro per ogni Comune (con quanti locali
   hanno già il menu di oggi); toccandolo si vedono solo i locali di quel Comune, con la casetta 🏠
   per tornare indietro. Il Comune scelto resta nell'indirizzo (#Noci): funzionano il tasto
   "indietro" del telefono, il ricaricamento e i preferiti. "Tutti" mostra tutti i locali. */
let TOWN=decodeURIComponent(location.hash.slice(1));
function townList(){{const c={{}};DATA.shops.forEach(s=>{{const k=s.comune||'Altro';c[k]=(c[k]||0)+1}});const col=new Intl.Collator('it',{{sensitivity:'base'}});return Object.keys(c).sort((a,b)=>(c[b]-c[a])||col.compare(a,b)).map(k=>[k,c[k]])}}
function buildTowns(){{const main=document.querySelector('main'),n=new Date(),iso=n.getFullYear()+'-'+String(n.getMonth()+1).padStart(2,'0')+'-'+String(n.getDate()).padStart(2,'0');main.querySelectorAll('.towncard').forEach(e=>e.remove());const first=main.firstChild;
const add=(name,label,tot,ok,cls)=>{{const b=document.createElement('button');b.type='button';b.className='towncard'+(cls?' '+cls:'')+(ok===tot?'':' none');b.dataset.town=name;b.innerHTML='<b></b>';const sp=(c,v)=>{{const e=document.createElement('span');e.className=c;e.textContent=v;return e}};b.children[0].append(label+' - ',sp('tok'+(ok===tot?' full':''),ok),'/',sp('ttot',tot));b.title=ok+' locali su '+tot+' con il menu di oggi';b.onclick=()=>{{location.hash=encodeURIComponent(name)}};main.insertBefore(b,first)}};
let all=0,allOk=0;townList().forEach(([k,c])=>{{const ok=DATA.shops.filter((s,i)=>(s.comune||'Altro')===k&&DATA.results[i].menu_date===iso).length;all+=c;allOk+=ok;add(k,k,c,ok,'')}});add('Tutti','Tutti i Comuni',all,allOk,'all')}}
function applyTown(){{const names=townList().map(x=>x[0]);if(TOWN&&TOWN!=='Tutti'&&!names.includes(TOWN))TOWN='';
document.querySelectorAll('.card').forEach(c=>{{const s=DATA.shops[+c.dataset.index];c.hidden=!TOWN||(TOWN!=='Tutti'&&(s.comune||'Altro')!==TOWN)}});
document.querySelectorAll('.towncard').forEach(e=>e.hidden=!!TOWN);const info=document.querySelector('.infocard');if(info)info.hidden=!!TOWN;document.querySelector('#home').hidden=!TOWN}}
window.addEventListener('hashchange',()=>{{TOWN=decodeURIComponent(location.hash.slice(1));if(!TOWN&&location.href.endsWith('#'))history.replaceState(null,'',location.pathname+location.search);if(dlg.open)dlg.close();refresh();window.scrollTo(0,0);if(document.activeElement)document.activeElement.blur()}});
document.querySelector('#home').onclick=()=>{{location.hash=''}};
function refresh(){{const n=new Date(),iso=n.getFullYear()+'-'+String(n.getMonth()+1).padStart(2,'0')+'-'+String(n.getDate()).padStart(2,'0');const gg=['Domenica','Lunedì','Martedì','Mercoledì','Giovedì','Venerdì','Sabato'],mm=['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto','settembre','ottobre','novembre','dicembre'];const dd=gg[n.getDay()]+' '+n.getDate()+' '+mm[n.getMonth()],lab=TOWN&&TOWN!=='Tutti'?TOWN:'';const t=lab?lab+' - '+dd:DATA.titolo.replace('{{data}}',dd);const h1=document.querySelector('h1'),cut=lab?lab.length:t.startsWith('Menu')?4:0,rest=t.slice(cut),dm=rest.match(/\\d+/);h1.textContent=t.slice(0,cut);const part=(x,c)=>{{if(!x)return;const e=document.createElement('span');if(c)e.className=c;e.textContent=x;h1.append(e)}};if(dm){{part(rest.slice(0,dm.index),'sub');part(dm[0],'');part(rest.slice(dm.index+dm[0].length),'sub')}}else part(rest,'sub');if(DATA.ora)part(' - '+DATA.ora,'sub');document.querySelectorAll('.card').forEach(c=>{{const i=+c.dataset.index,r=DATA.results[i],ok=r.menu_date===iso,st=c.querySelector('.status');c.classList.toggle('band-ok',ok);c.classList.toggle('band-old',!ok);st.className='status '+(ok?'fresh':r.menu_date?'stale':'missing');st.textContent=ok?'Oggi':r.menu_date?'Non di oggi':(r.error?'Errore':'Non disponibile')}});
/* ordine: prima le rosticcerie "Oggi" (fascia verde: menu, avviso di chiusura o riposo di oggi),
   poi le altre; in ciascun gruppo in ordine alfabetico. Rifatto anche quando cambia il giorno. */
const main=document.querySelector('main'),col=new Intl.Collator('it',{{sensitivity:'base'}}),has=i=>DATA.results[i].menu_date===iso;
[...document.querySelectorAll('.card')].sort((a,b)=>{{const x=+a.dataset.index,y=+b.dataset.index;return (has(y)-has(x))||col.compare(DATA.shops[x].nome,DATA.shops[y].nome)}}).forEach(c=>main.insertBefore(c,main.querySelector('.infocard')));buildTowns();applyTown()}}refresh();
/* clic sul titolo: ricarica la pagina dal server, senza cache (indirizzo con un numero sempre nuovo) */
document.querySelector('h1').addEventListener('click',()=>{{const u=new URL(location.href);u.searchParams.set('r',Date.now());location.replace(u.href)}});
let loaded=Date.now();document.addEventListener('visibilitychange',()=>{{if(document.visibilityState!=='visible')return;refresh();if(Date.now()-loaded>300000)location.reload()}})
/* Solo con ?v=57 nell'indirizzo: click di oggi in basso a destra di ogni scheda, totale nella scheda "Info";
   un tocco su "Info" apre la tabella Rosticceria / Oggi / Mese / Anno / Tutto con i totali.
   I numeri arrivano da Registro_click.gs (?azione=statistiche). Per non far aspettare, l'ultimo
   risultato è conservato sul dispositivo e mostrato subito, poi aggiornato appena arriva quello nuovo. */
if(ADMIN&&DATA.log){{const info=document.createElement('div');info.className='infocard';info.tabIndex=0;info.setAttribute('role','button');info.innerHTML='<div class="total">–</div><div><h2>Info</h2><small class="visits"></small></div>';document.querySelector('main').append(info);info.hidden=!!TOWN;
const sd=document.createElement('dialog');sd.id='stats';sd.innerHTML='<div class="modal-head"><h2>Clic per rosticceria</h2><button class="x" aria-label="Chiudi">✕</button></div><div class="statwrap"><table><thead><tr><th data-k="nome">Rosticceria</th><th data-k="oggi">Oggi</th><th data-k="mese">Mese</th><th data-k="anno">Anno</th><th data-k="tutto">Tutto</th></tr></thead><tbody></tbody><tfoot></tfoot></table><p class="statnote"></p></div>';document.body.append(sd);
sd.querySelector('.x').onclick=()=>sd.close();
/* ordine della tabella: alfabetico all'apertura; clic su un titolo = crescente per quella colonna,
   un altro clic sullo stesso titolo = decrescente; alla chiusura si torna all'ordine alfabetico */
const SRT={{k:null,d:1}};sd.querySelectorAll('th[data-k]').forEach(th=>{{th.onclick=()=>{{if(SRT.k===th.dataset.k&&SRT.d===1)SRT.d=-1;else{{SRT.k=th.dataset.k;SRT.d=1}}render()}}}});
sd.addEventListener('close',()=>{{SRT.k=null;SRT.d=1;render()}});sd.addEventListener('click',e=>{{if(e.target===sd)sd.close()}});
let ST=null,loading=false;try{{ST=JSON.parse(localStorage.getItem('menuStats')||'null')}}catch(e){{}}
const P=['oggi','mese','anno','tutto'],today=()=>new Date().toLocaleDateString('sv-SE',{{timeZone:'Europe/Rome'}});
function rowOf(d,n){{const ns=[n].concat((DATA.shops.find(s=>s.nome===n)||{{}}).nomi_precedenti||[]);const z={{}};P.forEach(k=>z[k]=0);ns.forEach(x=>{{const r=(d.righe||{{}})[x]||{{}};P.forEach(k=>z[k]+=r[k]||0)}});if(d.data!==today()){{z.oggi=0}}return z}}
function cell(t,v){{const c=document.createElement(t);c.textContent=v;return c}}
function render(){{if(!ST)return;document.querySelectorAll('.card').forEach(card=>{{const b=card.querySelector('.count');b.textContent=rowOf(ST,DATA.shops[+card.dataset.index].nome).oggi;b.hidden=false}});
const names=DATA.shops.map(s=>s.nome).sort(new Intl.Collator('it',{{sensitivity:'base'}}).compare),old=DATA.shops.flatMap(s=>s.nomi_precedenti||[]);Object.keys(ST.righe||{{}}).forEach(n=>{{if(!names.includes(n)&&!old.includes(n))names.push(n)}});const tot={{oggi:0,mese:0,anno:0,tutto:0}};
const ab=new Intl.Collator('it',{{sensitivity:'base'}}).compare;if(SRT.k)names.sort((a,b)=>(SRT.k==='nome'?ab(a,b):(rowOf(ST,a)[SRT.k]-rowOf(ST,b)[SRT.k]))*SRT.d||ab(a,b));
sd.querySelectorAll('th[data-k]').forEach(th=>{{th.dataset.dir=th.dataset.k===SRT.k?(SRT.d===1?'▲':'▼'):''}});
const tb=sd.querySelector('tbody');tb.innerHTML='';names.forEach(n=>{{const z=rowOf(ST,n);const tr=document.createElement('tr');tr.append(cell('td',n));P.forEach(k=>{{tot[k]+=z[k];tr.append(cell('td',z[k]))}});tb.append(tr)}});
const tf=sd.querySelector('tfoot');tf.innerHTML='';const tr=document.createElement('tr');tr.append(cell('td','Totale'));P.forEach(k=>tr.append(cell('td',tot[k])));tf.append(tr);
info.querySelector('.total').textContent=tot.oggi;const V=ST.visite&&ST.data===today()?ST.visite:(ST.visite?{{oggi:0,mese:ST.visite.mese,anno:ST.visite.anno,tutto:ST.visite.tutto}}:null);sd.querySelector('.statnote').textContent=(V?'Visite alla pagina: oggi '+V.oggi+' · mese '+V.mese+' · anno '+V.anno+' · tutto '+V.tutto+'. ':'')+(loading?'Aggiornamento in corso… ':'')+(ST.ora?'Dati delle '+ST.ora:'');const iv=info.querySelector('.visits');if(iv)iv.textContent=V?V.oggi+(V.oggi===1?' visita oggi':' visite oggi'):''}}
function loadStats(){{if(loading)return;loading=true;render();fetch(DATA.log+'?azione=statistiche&t='+Date.now(),{{cache:'no-store'}}).then(r=>r.json()).then(d=>{{if(!d||!d.righe)return;d.ora=new Date().toLocaleTimeString('it-IT',{{hour:'2-digit',minute:'2-digit'}});ST=d;try{{localStorage.setItem('menuStats',JSON.stringify(d))}}catch(e){{}}}}).catch(()=>{{}}).finally(()=>{{loading=false;render()}})}}
function openStats(){{render();sd.showModal();loadStats()}}info.addEventListener('click',openStats);info.addEventListener('keydown',e=>{{if(e.key==='Enter'||e.key===' '){{e.preventDefault();openStats()}}}});
render();loadStats();setInterval(loadStats,60000);document.addEventListener('visibilitychange',()=>{{if(document.visibilityState==='visible')loadStats()}})}}</script>
</body></html>'''
    OUTPUT.write_text(document, encoding="utf-8")
    WEB_PAGE.write_text(document, encoding="utf-8")
    ADMIN_PAGE.write_text(document, encoding="utf-8")




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


def short_address(shop: dict) -> str:
    """Indirizzo mostrato nella scheda: solo via e numero, un trattino e il Comune
    (es. "Via Nicola Losavio 10 - Putignano", "Via Mazzini 28 - Castellana"). In locali.json
    resta l'indirizzo completo (con CAP e provincia), che serve a Google Maps."""
    address = (shop.get("indirizzo") or "").strip()
    street = re.split(r"\s*,\s*|\s+-\s+", address, maxsplit=1)[0].strip()
    town = shop_town(shop)
    return f"{street} - {town}" if street and town else street


def shop_town(shop: dict) -> str:
    """Comune mostrato in alto a destra nel riquadro: campo "comune" di locali.json;
    se manca viene ricavato dall'indirizzo ("..., 70017 Putignano (BA)" oppure "... - Noci")."""
    town = (shop.get("comune") or "").strip()
    if town:
        return town
    address = (shop.get("indirizzo") or "").strip()
    match = re.search(r"\b\d{5}\s+([^,(]+?)\s*(?:\(|,|$)", address)
    if match:
        return match.group(1).strip()
    if " - " in address:
        return address.rsplit(" - ", 1)[1].strip()
    return ""


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
        "comune": input("Comune (mostrato in alto a destra nel riquadro): ").strip(),
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


def lock_owner_alive(lock: Path) -> bool:
    """True se il programma che ha creato il file di blocco (numero di processo scritto dentro)
    è ancora in esecuzione. Se la finestra del controllo è stata chiusa a metà, il blocco resta
    sul disco ma il processo non c'è più: quel blocco va ignorato e cancellato."""
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
                                                  ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return kernel.GetLastError() == 5  # accesso negato: il processo esiste
    try:
        code = ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # 259 = ancora attivo
            return False
        name = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            return "python" in name.value.lower()  # numero riusato da un altro programma: non conta
        return True
    finally:
        kernel.CloseHandle(handle)


def take_lock(max_wait_minutes: int = 20) -> bool:
    """Evita due esecuzioni contemporanee (giro automatico e giro manuale).

    Se un altro giro è in corso aspetta che finisca (al massimo max_wait_minutes), così
    l'orario fisso del controllo automatico viene rispettato anche dopo un controllo
    manuale. Un blocco più vecchio di 30 minuti è considerato abbandonato."""
    import time
    if AUTO_LOCK.exists() and not lock_owner_alive(AUTO_LOCK):
        AUTO_LOCK.unlink(missing_ok=True)  # giro precedente interrotto (finestra chiusa): blocco abbandonato
    if AUTO_LOCK.exists() and time.time() - AUTO_LOCK.stat().st_mtime >= 30 * 60:
        kill_previous_run()
    waited = False
    deadline = time.time() + max_wait_minutes * 60
    while AUTO_LOCK.exists() and lock_owner_alive(AUTO_LOCK) and time.time() - AUTO_LOCK.stat().st_mtime < 30 * 60:
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


PHONE_CONFIG = DATA / "notifica.json"  # {"bark": "https://api.day.app/CHIAVE"} - resta solo sul PC


def notify_phone(message: str) -> None:
    """Notifica sul cellulare con l'app Bark (iPhone): titolo "Menu", il testo delle novità e il
    suono "trebeep" (tre beep: basso, alto, basso; il file trebeep.caf va caricato
    una volta nell'app). Toccando la notifica si apre il sito. L'indirizzo con la chiave personale
    sta in dati/notifica.json, che non viene pubblicato su GitHub. Senza quel file non fa nulla."""
    base = str(read_json(PHONE_CONFIG, {}).get("bark", "")).strip().rstrip("/")
    if not base:
        return
    if not base.startswith("http"):
        base = f"https://api.day.app/{base}"  # basta anche la sola chiave
    payload = {"title": "Menu", "body": message, "sound": "trebeep", "group": "Menu", "url": WEB_URL}
    try:
        request = urllib.request.Request(base, data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
        print("  notifica inviata al cellulare")
    except Exception as exc:  # una notifica non riuscita non deve fermare il programma
        print(f"  notifica al cellulare non riuscita: {exc}")


OVERVIEW = DATA / "panorama"  # PDF creati dal pulsante "Panorama" del Monitor


def make_overview(config: dict[str, Any], browser: "BrowserCollector") -> Path:
    """Pulsante "Panorama" del Monitor: un solo PDF con, per ogni locale, la scheda come appare
    sul sito (a sinistra) e la fonte come appare adesso (a destra: post Facebook, storia
    Facebook/Instagram o sito del locale). Prima pagina: la pagina iniziale del sito."""
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont
    shops = config.get("locali", [])
    FACEBOOK_LOGIN.clear()
    FACEBOOK_LOGIN.update(find_facebook_login(shops, config.get("impostazioni", {})))
    states = {item.get("id"): item for item in read_json(STATE, {}).get("results", [])}
    width, height, margin = 1754, 1240, 40  # A4 orizzontale a 150 dpi

    def font(size: int) -> Any:
        for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def paste(sheet: Any, data: bytes | None, box: tuple[int, int, int, int], empty: str) -> None:
        x0, y0, x1, y1 = box
        draw = ImageDraw.Draw(sheet)
        draw.rectangle(box, outline="#94a3b8", width=2)
        if not data:
            draw.text((x0 + 20, y0 + 20), empty, fill="#b91c1c", font=font(28))
            return
        picture = Image.open(BytesIO(data)).convert("RGB")
        picture.thumbnail((x1 - x0 - 8, y1 - y0 - 8))
        sheet.paste(picture, (x0 + (x1 - x0 - picture.width) // 2, y0 + (y1 - y0 - picture.height) // 2))

    site = (ROOT / "Menu.html").as_uri() + "?v=57"  # versione amministratore: le schede aperte NON contano come click
    pages = []
    browser.start({})
    page = browser.context.new_page()
    page.set_viewport_size({"width": 1100, "height": 1300})
    page.goto(site + "#Tutti", wait_until="load")
    page.wait_for_timeout(1500)
    cover = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(cover).text((margin, margin), f"Panorama menu - {datetime.now():%d/%m/%Y %H:%M}",
                               fill="#0f172a", font=font(44))
    paste(cover, page.screenshot(type="jpeg", quality=80, full_page=True),
          (margin, 120, width - margin, height - margin), "")
    pages.append(cover)
    shots: dict[str, bytes | None] = {}
    for shop in shops:
        try:
            # la pagina elenca i locali in un altro ordine (prima quelli aggiornati): la scheda si
            # cerca per NOME, e si fotografa solo quando il titolo mostra davvero quel locale
            found = page.evaluate("name => { const i = DATA.shops.findIndex(s => s.nome === name);"
                                  " if (i >= 0) openCard(i); return i; }", shop["nome"])
            if found < 0:
                raise RuntimeError("locale non presente nella pagina")
            page.wait_for_function("name => (document.querySelector('#name').textContent || '').startsWith(name)",
                                   arg=shop["nome"], timeout=5000)
            page.wait_for_timeout(1500)
            shots[shop["id"]] = page.locator("#detail .sheet").screenshot(type="jpeg", quality=80)
            page.evaluate("document.querySelector('#detail').close()")
        except Exception:
            shots[shop["id"]] = None
    page.close()
    today = date.today().isoformat()
    for index, shop in enumerate(shops, 1):
        print(f"[{index}/{len(shops)}] {shop['nome']}")
        source = next((with_facebook_login(item) for item in shop.get("fonti", [])
                       if item.get("attiva", True) and item.get("tipo") not in ("cartella", "immagine")
                       and item.get("url")), None)
        origin = browser.snapshot_source(shop, source) if source else None
        state = states.get(shop["id"], {})
        day = state.get("menu_date", "")
        sheet = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(sheet)
        colour = "#15803d" if day == today else "#c2410c"
        status = "menu di OGGI" if day == today else (f"ultimo menu del {day[8:10]}/{day[5:7]}" if day else "nessun menu")
        draw.rectangle((0, 0, 14, height), fill=colour)
        draw.text((margin, 24), f"{shop['nome']}  -  {shop.get('comune', '')}", fill="#0f172a", font=font(44))
        draw.text((margin, 84), f"{status}   (fonte: {state.get('source', '-')})", fill=colour, font=font(30))
        draw.text((margin, 130), "Sul sito", fill="#334155", font=font(26))
        draw.text((width // 2 + 10, 130), f"Fonte adesso: {source.get('nome', '') if source else '-'}  "
                  f"{(source or {}).get('url', '')[:70]}", fill="#334155", font=font(22))
        paste(sheet, shots.get(shop["id"]), (margin, 170, width // 2 - 10, height - margin), "scheda non disponibile")
        paste(sheet, origin, (width // 2 + 10, 170, width - margin, height - margin),
              "fonte non disponibile" if source else "nessuna fonte online")
        pages.append(sheet)
    OVERVIEW.mkdir(parents=True, exist_ok=True)
    target = OVERVIEW / f"Panorama_{datetime.now():%Y-%m-%d_%H%M}.pdf"
    pages[0].save(target, "PDF", resolution=150, save_all=True, append_images=pages[1:])
    for old in sorted(OVERVIEW.glob("Panorama_*.pdf"))[:-10]:
        old.unlink(missing_ok=True)  # si tengono solo le ultime 10
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Raccoglie e pubblica menu da fonti configurabili.")
    parser.add_argument("--aggiungi", action="store_true", help="Aggiunge una rosticceria con procedura guidata.")
    parser.add_argument("--solo-html", action="store_true", help="Non prova fonti online; usa i file disponibili.")
    parser.add_argument("--visibile", action="store_true", help="Mostra il browser durante le acquisizioni online.")
    parser.add_argument("--pubblica", action="store_true", help="Dopo l'aggiornamento invia la pagina a GitHub Pages.")
    parser.add_argument("--salta-aggiornati", action="store_true",
                        help="Non ricontrolla online le rosticcerie che hanno già il menu di oggi "
                             "(pulsante \"Controlla\" della finestra di controllo; il giro ogni 15 minuti fa lo stesso).")
    parser.add_argument("--solo", metavar="ID",
                        help="Segnala una rosticceria (id di locali.json) come probabilmente sbagliata e la "
                             "ricontrolla a fondo: il menu di oggi va in dati/scartati e quella foto non verrà più "
                             "usata; le altre restano come all'ultimo giro (clic su una rosticceria nel Monitor).")
    parser.add_argument("--login", action="store_true", help="Apre Facebook e Instagram per salvare la sessione.")
    parser.add_argument("--prova-beep", action="store_true", help="Fa sentire i tre beep e termina.")
    parser.add_argument("--prova-notifica", action="store_true",
                        help="Manda una notifica di prova al cellulare (app Bark, tre beep) e termina.")
    parser.add_argument("--panorama", "--panoramica", dest="panorama", action="store_true",
                        help="Crea un PDF con la scheda di ogni locale sul sito e la sua fonte come appare adesso "
                             "(pulsante \"Panorama\" del Monitor) e lo apre.")
    parser.add_argument("--ocr", metavar="IMMAGINE", help="Mostra il testo letto in una foto e l'eventuale chiusura.")
    parser.add_argument("--automatico", action="store_true",
                        help="Per l'attività pianificata: solo nella fascia oraria, senza finestre, con log e pubblicazione.")
    args = parser.parse_args()
    if args.prova_beep:
        beep_three_times()
        return
    if args.prova_notifica:
        if not read_json(PHONE_CONFIG, {}).get("bark"):
            print(f"Manca {PHONE_CONFIG}: vedi LEGGIMI.txt > Notifica sul cellulare.")
        notify_phone("Prova: le notifiche del Menu funzionano")
        return
    if args.ocr:
        text = ocr_image(Path(args.ocr))
        print(f"Testo letto: {text or '(nessuno)'}")
        closed = closure_period(text)
        print(f"Chiusura: dal {closed[0]:%d/%m} al {closed[1]:%d/%m}" if closed else "Chiusura: nessuna")
        return
    make_folders()
    config = read_json(CONFIG, {"impostazioni": {}, "locali": []})
    if args.panorama:
        if not take_lock():
            print("Un controllo è in corso da oltre 20 minuti: riprova più tardi.")
            return
        browser = BrowserCollector(visible=False)
        try:
            print("Panorama: fotografo il sito e le fonti di ogni locale (qualche minuto)...")
            target = make_overview(config, browser)
            print(f"Creato: {target}")
            if sys.platform == "win32":
                os.startfile(target)  # apre il PDF
        finally:
            browser.stop()
            AUTO_LOCK.unlink(missing_ok=True)
        return
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
    only = next((shop.get("nome", args.solo) for shop in config.get("locali", []) if shop.get("id") == args.solo), args.solo)
    start_manual_log(f"ricontrollo di {only}" if args.solo else "pubblicazione" if args.solo_html
                     else "controllo" if args.salta_aggiornati else "ricontrollo")
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
    if kind.startswith("ricontrollo di "):
        print(f"RICONTROLLO di {kind[15:]}: solo questa rosticceria, le altre restano come all'ultimo giro")
    else:
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
    FACEBOOK_LOGIN.clear()
    FACEBOOK_LOGIN.update(find_facebook_login(shops, settings))
    ALL_SHOPS[:] = shops
    SUSPECT.clear()
    if args.solo:
        SUSPECT.add(args.solo)  # clic sul nome nel Monitor = "questo locale ha probabilmente un menu sbagliato"
    results: list[dict[str, Any]] = []
    previous_sources = {item.get("id"): item.get("source", "") for item in read_json(STATE, {}).get("results", [])}
    # stesso ordine della finestra di controllo e del sito: prima quelle già aggiornate oggi
    # (secondo l'ultimo giro), poi le altre; in ciascun gruppo in ordine alfabetico
    previous_days = {item.get("id"): item.get("menu_date", "") for item in read_json(STATE, {}).get("results", [])}
    shops = sorted(shops, key=lambda item: (previous_days.get(item["id"]) != date.today().isoformat(),
                                            sort_key_name(item.get("nome", ""))))
    previous_records = {item.get("id"): item for item in read_json(STATE, {}).get("results", [])}
    try:
        for shop in shops:
            if args.solo and shop["id"] != args.solo and shop["id"] in previous_records:
                results.append(previous_records[shop["id"]])  # --solo: le altre restano come all'ultimo giro
                continue
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
        notify_phone("Novità: " + ", ".join(news))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nOperazione annullata.")
