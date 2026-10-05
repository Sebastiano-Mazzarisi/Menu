"""Monitor.py - piccola finestra per tenere d'occhio l'aggiornamento automatico dei menu.

Mostra, sempre in primo piano e spostabile sul desktop:
  - quanto manca al prossimo controllo automatico (attività pianificata "Menu" di Windows)
  - se un controllo è in corso in questo momento
  - per ogni rosticceria: aggiornata oggi (verde) oppure no (arancione/rosso)
  - l'esito dell'ultimo giro (pubblicato, nessuna modifica, errore)

Avvio: doppio clic su Monitor.bat (oppure: pythonw Monitor.py).
Usa solo la libreria standard di Python (tkinter). Vedi LEGGIMI.txt.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "locali.json"
STATE = ROOT / "dati" / "stato.json"
LOG = ROOT / "dati" / "automatico.log"
LOCK = ROOT / "dati" / "automatico.lock"
POSITION = ROOT / "dati" / "monitor.json"
TASK = "Menu"
CONSOLE_TITLE = "Menu - controllo manuale"
INTERVAL = timedelta(minutes=15)
WEB_URL = "https://sebastiano-mazzarisi.github.io/Menu/"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BG, FG, MUTED = "#0b1220", "#e2e8f0", "#94a3b8"
GREEN, ORANGE, RED, BLUE = "#22c55e", "#f97316", "#ef4444", "#60a5fa"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def task_info() -> tuple[datetime | None, str]:
    """Chiede a Windows la prossima esecuzione dell'attività "Menu" (formato indipendente dalla lingua)."""
    command = (f"$t = Get-ScheduledTask -TaskName '{TASK}' -ErrorAction Stop; "
               f"$i = Get-ScheduledTaskInfo -TaskName '{TASK}'; "
               "$n = if ($i.NextRunTime) {{ $i.NextRunTime.ToString('yyyy-MM-ddTHH:mm:ss') }} else {{ '' }}; "
               "$n + '|' + $t.State").replace("{{", "{").replace("}}", "}")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True,
                             text=True, timeout=20, creationflags=NO_WINDOW).stdout.strip()
        when, state = out.split("|", 1)
        return (datetime.fromisoformat(when) if when else None), state
    except Exception:
        return None, "assente"


def pythonw() -> str:
    """Percorso di pythonw.exe (Python senza finestra) accanto all'interprete in uso."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def schedule_task() -> str:
    """Crea (o ricrea) l'attività "Menu": ogni 15 minuti a orari fissi, a partire dalle 07:00
    (07:00, 07:15, 07:30 ...). Restituisce un messaggio di esito."""
    action = f'"{pythonw()}" "{ROOT / "Menu.py"}" --automatico'
    done = subprocess.run(["schtasks", "/Create", "/F", "/TN", TASK, "/SC", "MINUTE", "/MO", "15",
                           "/ST", "07:00", "/TR", action], capture_output=True, text=True,
                          creationflags=NO_WINDOW)
    return "Pianificazione attivata" if done.returncode == 0 else "Pianificazione NON riuscita"


def disable_task() -> str:
    done = subprocess.run(["schtasks", "/Change", "/TN", TASK, "/DISABLE"], capture_output=True,
                          text=True, creationflags=NO_WINDOW)
    return "Pianificazione disabilitata" if done.returncode == 0 else "Disabilitazione NON riuscita"


def in_window(moment: datetime, settings: dict) -> bool:
    window = settings.get("automatico", {})
    hhmm = moment.strftime("%H:%M")
    return window.get("dalle", "00:00") <= hhmm <= window.get("alle", "23:59")


def next_effective(next_run: datetime, settings: dict) -> datetime:
    """Prima esecuzione che cade dentro la fascia oraria (fuori fascia Menu.py non fa nulla)."""
    moment = next_run
    for _ in range(200):
        if in_window(moment, settings):
            return moment
        moment += INTERVAL
    return next_run


def last_outcome() -> tuple[str, str]:
    """Ora ed esito dell'ultimo giro, letti dal registro dati/automatico.log."""
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        return "", "nessun giro registrato"
    start = max((i for i, line in enumerate(lines) if line.startswith("=== ")), default=None)
    if start is None:
        return "", "nessun giro registrato"
    when = lines[start].strip("= ").strip()[11:16]
    if "(manuale)" in lines[start]:
        when += " (manuale)"
    block = lines[start + 1:]
    for line in block:
        if line.startswith("ERRORE") or "NON riuscita" in line:
            return when, "errore (vedi registro)"
    news = next((line[8:].strip() for line in block if line.startswith("Novità:")), "")
    if news:
        return when, f"novità: {news}"
    for line in block:
        if line.startswith("Pubblicato"):
            return when, "pubblicato"
        if "nessuna modifica" in line:
            return when, "nessuna novità"
        if "ancora in corso" in line or "bloccat" in line:
            return when, "saltato (giro precedente in corso)"
    return when, "in corso…" if LOCK.exists() else "completato"


DIGITS = {  # cifre 5x7 disegnate a mano: niente librerie esterne
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
}
ICON_DIR = ROOT / "dati" / "icone_monitor"


def badge_pixels(updated: int, total: int, size: int) -> list[list[str]]:
    """Matrice di colori del quadratino: bordo colorato, fondo scuro, numero bianco."""
    colour = GREEN if total and updated == total else ORANGE if updated else "#64748b"
    border = max(1, size // 10)
    grid = [[colour if min(x, y, size - 1 - x, size - 1 - y) < border else BG for x in range(size)]
            for y in range(size)]
    text = str(updated)
    inner = size - 2 * border
    scale = max(1, min(inner // 7, inner // (6 * len(text) - 1)))  # numero il più grande possibile
    width, height = (6 * len(text) - 1) * scale, 7 * scale
    left, top = (size - width) // 2, (size - height) // 2
    for index, char in enumerate(text):
        for row, bits in enumerate(DIGITS[char]):
            for col, bit in enumerate(bits):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            x = left + (index * 6 + col) * scale + dx
                            y = top + row * scale + dy
                            if 0 <= x < size and 0 <= y < size:
                                grid[y][x] = "#ffffff"
    return grid


def make_badge(updated: int, total: int, size: int, master=None) -> tk.PhotoImage:
    """Quadratino come immagine tkinter (solo libreria standard)."""
    image = tk.PhotoImage(master=master, width=size, height=size)
    rows = badge_pixels(updated, total, size)
    image.put(" ".join("{" + " ".join(row) + "}" for row in rows), to=(0, 0))
    return image


def write_ico(images: list[tk.PhotoImage], path: Path) -> None:
    """File .ico con le immagini PNG dentro (Windows usa il .ico anche per la barra delle applicazioni)."""
    import struct
    import tempfile

    blobs = []
    for image in images:
        with tempfile.TemporaryDirectory() as folder:
            png = Path(folder) / "i.png"
            image.write(str(png), format="png")
            blobs.append((image.width(), png.read_bytes()))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b"", b""
    for size, blob in blobs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    path.write_bytes(header + entries + data)


def log_error(text: str) -> None:
    try:
        with (ROOT / "dati" / "monitor.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


class Monitor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Menu")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        # finestra normale: ha il pulsante "riduci a icona" e compare nella barra delle applicazioni
        self.icon_images: list = []  # riferimenti alle icone (altrimenti tkinter le cancella)
        self.icon_count: tuple[int, int] | None = None
        self.set_icon(0, 0)
        saved = read_json(POSITION, {})
        if "x" in saved:
            self.geometry(f"+{saved['x']}+{saved['y']}")
        self.protocol("WM_DELETE_WINDOW", self.close)

        font = ("Segoe UI", 10)
        self.countdown = tk.Label(self, font=("Segoe UI", 20, "bold"), bg=BG, fg=FG)
        self.countdown.pack(padx=14, pady=(10, 0), anchor="w")
        self.subtitle = tk.Label(self, font=font, bg=BG, fg=MUTED, justify="left")
        self.subtitle.pack(padx=14, anchor="w")
        self.summary = tk.Label(self, font=("Segoe UI", 10, "bold"), bg=BG, fg=FG)
        self.summary.pack(padx=14, pady=(8, 2), anchor="w")
        self.rows = tk.Frame(self, bg=BG)
        self.rows.pack(padx=14, anchor="w", fill="x")
        self.outcome = tk.Label(self, font=font, bg=BG, fg=MUTED, justify="left")
        self.outcome.pack(padx=14, pady=(8, 4), anchor="w")

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(padx=10, pady=(2, 10), anchor="w")
        for text, action in (("Controlla ora", self.run_now), ("Registro", self.open_log), ("Sito", self.open_site),
                             ("Pianifica", self.toggle_task)):
            button = tk.Button(buttons, text=text, command=action, font=("Segoe UI", 9), bg="#1e293b", fg=FG,
                               activebackground="#334155", activeforeground=FG, relief="flat", padx=8)
            button.pack(side="left", padx=4)
        self.toggle_button = button  # l'ultimo: "Pianifica" oppure "Disabilita"
        self.message, self.message_until = "", datetime.min
        self.querying = False

        self.next_run: datetime | None = None
        self.task_state = ""
        self.last_query = datetime.min
        self.tick()

    # --- aggiornamento --------------------------------------------------------------------------
    def tick(self) -> None:
        now = datetime.now()
        config = read_json(CONFIG, {})
        settings = config.get("impostazioni", {})
        # chiede a Windows ogni minuto, oppure subito dopo che l'esecuzione prevista è passata
        if not self.querying and ((now - self.last_query).total_seconds() > 60
                                  or (self.next_run and now > self.next_run + timedelta(seconds=5))):
            self.querying = True  # domanda a Windows in un thread: la finestra non si blocca
            self.last_query = now
            threading.Thread(target=self.query_task, daemon=True).start()
        active = self.is_active()
        self.toggle_button.config(text="Disabilita" if active else "Pianifica")

        if LOCK.exists():
            self.countdown.config(text="Controllo in corso…", fg=BLUE)
            self.subtitle.config(text="sto leggendo Facebook, Instagram e i siti")
        elif self.next_run is None:
            self.countdown.config(text="Non pianificato", fg=RED)
            self.subtitle.config(text="premi il pulsante Pianifica")
        elif self.task_state.lower() == "disabled":
            self.countdown.config(text="Disattivato", fg=RED)
            self.subtitle.config(text="premi il pulsante Pianifica per riattivare")
        else:
            target = next_effective(self.next_run, settings)
            seconds = max(0, int((target - now).total_seconds()))
            hours, rest = divmod(seconds, 3600)
            text = f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"
            self.countdown.config(text=text, fg=FG)
            note = "" if active else "\norari non allineati ai quarti d'ora: premi Pianifica"
            self.subtitle.config(text=f"al prossimo controllo (ore {target:%H:%M}){note}")

        self.show_shops(config)
        when, outcome = last_outcome()
        colour = RED if outcome.startswith("errore") else GREEN if outcome.startswith("novità") else MUTED
        text = f"Ultimo giro {when}: {outcome}" if when else outcome
        if self.message and now < self.message_until:
            text += f"\n{self.message}"
        self.outcome.config(text=text, fg=colour)
        self.after(1000, self.tick)

    def show_shops(self, config: dict) -> None:
        results = {item.get("id"): item for item in read_json(STATE, {}).get("results", [])}
        today = date.today().isoformat()
        signature = (today, json.dumps(config.get("locali", [])), json.dumps(results, sort_keys=True))
        if signature == getattr(self, "_signature", None):
            return  # niente di nuovo: non ridisegno (evita lo sfarfallio)
        self._signature = signature
        for child in self.rows.winfo_children():
            child.destroy()
        updated = 0
        shops = config.get("locali", [])
        for shop in shops:
            result = results.get(shop.get("id"), {})
            day = result.get("menu_date", "")
            if day == today:
                updated += 1
                mark, colour, info = "●", GREEN, "oggi"
            elif day:
                mark, colour, info = "●", ORANGE, "/".join(reversed(day.split("-")[1:]))
            else:
                mark, colour, info = "●", RED, "nessuno"
            row = tk.Frame(self.rows, bg=BG)
            row.pack(fill="x")
            tk.Label(row, text=mark, fg=colour, bg=BG, font=("Segoe UI", 11)).pack(side="left")
            tk.Label(row, text=shop.get("nome", "?"), fg=FG, bg=BG, font=("Segoe UI", 10), width=22,
                     anchor="w").pack(side="left")
            tk.Label(row, text=info, fg=colour, bg=BG, font=("Segoe UI", 10)).pack(side="left")
        self.summary.config(text=f"Aggiornate oggi: {updated} su {len(shops)}")
        self.set_icon(updated, len(shops))

    def is_active(self) -> bool:
        """Attività attiva e con orari fissi (:00, :15, :30, :45). Una vecchia attività creata
        a un'ora qualsiasi va ricreata con Pianifica."""
        running = self.task_state.lower() in ("ready", "running", "queued")
        aligned = self.next_run is None or self.next_run.minute % 15 == 0
        return running and aligned

    def set_icon(self, updated: int, total: int) -> None:
        """Icona della barra delle applicazioni: quadratino con il numero di rosticcerie aggiornate.

        Bordo verde se sono tutte aggiornate, arancione se ne manca qualcuna, grigio se zero.
        Anche il titolo riporta il conteggio (es. "4/8 Menu"). Eventuali errori finiscono
        in dati/monitor.log."""
        if self.icon_count == (updated, total):
            return
        self.icon_count = (updated, total)
        self.title(f"{updated}/{total} Menu" if total else "Menu")
        try:
            images = [make_badge(updated, total, size, self) for size in (64, 48, 32, 16)]
            self.icon_images = images
            self.iconphoto(True, *images)
            if sys.platform == "win32":
                # su Windows la barra delle applicazioni usa l'icona .ico della finestra
                ICON_DIR.mkdir(parents=True, exist_ok=True)
                ico = ICON_DIR / f"badge_{updated}_{total}.ico"
                if not ico.exists():
                    write_ico(images, ico)
                self.iconbitmap(default=str(ico))
                self.ico_path = ico
                self.after(200, self.force_taskbar_icon)  # dopo che la finestra è comparsa
        except Exception as exc:
            import traceback
            log_error(f"icona non impostata: {exc!r}\n{traceback.format_exc()}")

    def force_taskbar_icon(self) -> None:
        """Imposta l'icona direttamente con le funzioni di Windows (WM_SETICON) sulla finestra
        "contenitore" creata da tkinter: è quella che la barra delle applicazioni mostra."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.GetParent.restype = ctypes.c_void_p
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            image_icon, load_from_file, wm_seticon = 1, 0x10, 0x80
            big = user32.LoadImageW(None, str(self.ico_path), image_icon, 48, 48, load_from_file)
            small = user32.LoadImageW(None, str(self.ico_path), image_icon, 16, 16, load_from_file)
            if not big:
                log_error(f"LoadImageW fallita per {self.ico_path}")
                return
            user32.SendMessageW(hwnd, wm_seticon, 1, big)    # ICON_BIG: barra delle applicazioni
            user32.SendMessageW(hwnd, wm_seticon, 0, small)  # ICON_SMALL: barra del titolo
        except Exception as exc:
            log_error(f"icona Windows non impostata: {exc!r}")

    def say(self, message: str) -> None:
        """Messaggio temporaneo (10 secondi) sotto l'esito dell'ultimo giro."""
        self.message, self.message_until = message, datetime.now() + timedelta(seconds=10)

    def query_task(self) -> None:
        self.next_run, self.task_state = task_info()
        self.querying = False

    # --- pulsanti -------------------------------------------------------------------------------
    def run_now(self) -> None:
        """Lancia subito un giro completo in una finestra (come Avvia.bat).

        La finestra resta aperta per leggere l'esito; prima di aprirne una nuova viene chiusa
        quella del controllo manuale precedente (riconosciuta dal titolo)."""
        bat = ROOT / "Avvia.bat"
        if not bat.exists() or sys.platform != "win32":
            return
        if LOCK.exists():
            self.say("Un controllo è già in corso: attendi che finisca")
            return
        subprocess.run(["taskkill", "/F", "/T", "/FI", f"WINDOWTITLE eq {CONSOLE_TITLE}*"],
                       capture_output=True, creationflags=NO_WINDOW)
        subprocess.Popen(["cmd", "/k", f"title {CONSOLE_TITLE} && call \"{bat}\""], cwd=ROOT,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)

    def open_log(self) -> None:
        """Apre il registro con il Blocco note (se non esiste ancora lo dice nella finestra)."""
        if not LOG.exists():
            self.say("Registro non ancora creato: nessun giro finora")
            return
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(LOG)])

    def toggle_task(self) -> None:
        """Pianifica (se l'attività manca o è disabilitata) oppure Disabilita."""
        if self.is_active():
            self.say(disable_task())
        else:
            self.say(schedule_task())
        self.task_state = "..."
        self.last_query = datetime.min  # rilegge subito lo stato da Windows

    def open_site(self) -> None:
        webbrowser.open(WEB_URL + "?v=57")  # amministratore: i click non vengono registrati

    def close(self) -> None:
        try:
            POSITION.parent.mkdir(exist_ok=True)
            POSITION.write_text(json.dumps({"x": self.winfo_x(), "y": self.winfo_y()}), encoding="utf-8")
        finally:
            self.destroy()


if __name__ == "__main__":
    if sys.platform == "win32":
        # identità propria per la barra delle applicazioni: mostra la nostra icona, non quella di Python
        try:
            import ctypes
            result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mazzarisi.Menu.Monitor")
            if result != 0:
                log_error(f"AppUserModelID non impostato (codice {result})")
        except Exception as exc:
            log_error(f"AppUserModelID non impostato: {exc!r}")
    Monitor().mainloop()
