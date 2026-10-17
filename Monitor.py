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
import time
import threading
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
import unicodedata

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "locali.json"
STATE = ROOT / "dati" / "stato.json"
LOG = ROOT / "dati" / "automatico.log"
ORDER_FILE = ROOT / "dati" / "ordine_monitor.json"  # ordine delle righe, letto da Menu.py all'avvio del giro
IN_PROGRESS = ROOT / "dati" / "in_corso.json"  # locale che Menu.py sta controllando adesso
LOCK = ROOT / "dati" / "automatico.lock"
POSITION = ROOT / "dati" / "monitor.json"
TASK = "Menu"
CONSOLE_TITLE = "Menu - controllo manuale"
CONSOLE_SECONDS = 60  # la finestra DOS del controllo manuale si chiude da sola dopo questi secondi
WEB_URL = "https://sebastiano-mazzarisi.github.io/Menu/"
FACEBOOK_URL = "https://www.facebook.com/MenuRosticcerie"  # pagina Facebook "Menu rosticcerie"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BG, FG, MUTED = "#0b1220", "#e2e8f0", "#94a3b8"
GREEN, ORANGE, RED, BLUE = "#22c55e", "#f97316", "#ef4444", "#60a5fa"
YELLOW = "#ffe600"  # pallina del locale in controllo (lampeggia)


# Script PowerShell del pulsante "Sito": cerca con UI Automation di Windows (nessuna installazione)
# una finestra di Chrome/Edge con una scheda intitolata "Menu" (il titolo del sito) oppure la
# finestra dell'app "Menu" aggiunta dal browser; la porta in primo piano, seleziona la scheda e
# preme F5 (aggiorna). Scrive "scheda", "app" o "no" (allora il Monitor apre il sito come prima).
SITE_SCRIPT = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms
Add-Type @'
using System; using System.Runtime.InteropServices;
public class MenuWin {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, int f, int e);
}
'@
$A = [System.Windows.Automation.AutomationElement]
function Davanti($h) {
  if ([MenuWin]::IsIconic($h)) { [MenuWin]::ShowWindow($h, 9) | Out-Null }
  [MenuWin]::keybd_event(0x12, 0, 0, 0); [MenuWin]::keybd_event(0x12, 0, 2, 0)  # ALT: Windows lascia cambiare finestra
  [MenuWin]::SetForegroundWindow($h) | Out-Null
}
function Aggiorna { Start-Sleep -Milliseconds 400; [System.Windows.Forms.SendKeys]::SendWait('{F5}') }
$win = New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'Chrome_WidgetWin_1')
$tab = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)
foreach ($w in $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $win)) {
  $h = [IntPtr]$w.Current.NativeWindowHandle
  if ($w.Current.Name -eq 'Menu') { Davanti $h; Aggiorna; 'app'; exit }
}
foreach ($w in $A::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $win)) {
  $h = [IntPtr]$w.Current.NativeWindowHandle
  foreach ($t in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $tab)) {
    if ($t.Current.Name -match '^Menu(\s|$|-)') {
      Davanti $h
      try { $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select() } catch {}
      Aggiorna; 'scheda'; exit
    }
  }
}
'no'
"""


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


def plan(settings: dict) -> tuple[str, str, int]:
    """Orari del controllo automatico da locali.json > impostazioni > automatico
    (come in Menu.py: predefiniti 08:00-20:00 ogni 30 minuti)."""
    window = settings.get("automatico", {})
    try:
        every = max(5, int(window.get("ogni_minuti", 30)))
    except (TypeError, ValueError):
        every = 30
    return window.get("dalle", "08:00"), window.get("alle", "20:00"), every


def schedule_task() -> str:
    """Crea (o ricrea) l'attività "Menu" con gli orari di locali.json: lo fa Menu.py --pianifica,
    così Monitor, Pianifica.bat e il giro automatico usano la stessa regola."""
    python = Path(sys.executable).with_name("python.exe")
    exe = str(python) if python.exists() else sys.executable
    done = subprocess.run([exe, str(ROOT / "Menu.py"), "--pianifica"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    dalle, alle, every = plan(read_json(CONFIG, {}).get("impostazioni", {}))
    return (f"Pianificazione attivata: ogni {every} minuti dalle {dalle} alle {alle}"
            if done.returncode == 0 and "NON" not in done.stdout else "Pianificazione NON riuscita")


def disable_task() -> str:
    done = subprocess.run(["schtasks", "/Change", "/TN", TASK, "/DISABLE"], capture_output=True,
                          text=True, creationflags=NO_WINDOW)
    return "Pianificazione disabilitata" if done.returncode == 0 else "Disabilitazione NON riuscita"


def in_window(moment: datetime, settings: dict) -> bool:
    dalle, alle, _ = plan(settings)
    return dalle <= moment.strftime("%H:%M") <= alle


def next_effective(next_run: datetime, settings: dict) -> datetime:
    """Prima esecuzione che cade dentro la fascia oraria (fuori fascia Menu.py non fa nulla)."""
    moment = next_run
    for _ in range(200):
        if in_window(moment, settings):
            return moment
        moment += timedelta(minutes=plan(settings)[2])
    return next_run


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


def check_running() -> bool:
    """Un controllo è davvero in corso? Il file di blocco da solo non basta: se il controllo è
    stato interrotto (finestra chiusa a metà) il file resta. In quel caso lo cancello."""
    if not LOCK.exists():
        return False
    try:
        old = time.time() - LOCK.stat().st_mtime >= 30 * 60
    except OSError:
        old = False
    # come Menu.py: un blocco di più di 30 minuti è di un controllo rimasto appeso (o di un numero
    # di processo riusato da un altro programma). Prima il pulsante non faceva nulla per ore.
    if lock_owner_alive(LOCK) and not old:
        return True
    try:
        LOCK.unlink()
    except OSError:
        pass
    return False


def last_outcome() -> tuple[str, str]:
    """Ora ed esito dell'ultimo giro; se in quel giro c'era il riassunto per Facebook (11:30)
    si aggiunge anche il suo esito su una seconda riga."""
    when, outcome = round_outcome()
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        start = max((i for i, line in enumerate(lines) if line.startswith("=== ")), default=None)
        block = lines[start + 1:] if start is not None else []
    except OSError:
        block = []
    note = ""
    for line in block:
        if line.startswith("Riassunto pubblicato su Facebook"):
            note = "riassunto pubblicato su Facebook"
        elif line.startswith("Riassunto NON pubblicato su Facebook"):
            note = "riassunto NON pubblicato su Facebook (vedi registro)"
        elif line.startswith("Riassunto NON pubblicato:"):
            note = "riassunto creato in dati/riassunti (Facebook non collegato)"
    return when, f"{outcome}\n{note}" if note else outcome


def round_outcome() -> tuple[str, str]:
    """Ora ed esito dell'ultimo giro, letti dal registro dati/automatico.log."""
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        return "", "nessun giro registrato"
    start = max((i for i, line in enumerate(lines) if line.startswith("=== ")), default=None)
    if start is None:
        return "", "nessun giro registrato"
    when = lines[start].strip("= ").strip()[11:16]
    if "(manuale" in lines[start]:
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
    return when, "in corso…" if check_running() else "completato"


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


def smooth_badge_png(updated: int, total: int, size: int) -> bytes | None:
    """Quadratino disegnato con Pillow: carattere vero (Arial/Segoe grassetto), disegnato 8 volte
    più grande e poi rimpicciolito, così bordi e numero sono nitidi e non seghettati.
    Restituisce il PNG, oppure None se Pillow non c'è."""
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    scale = 8
    big = size * scale
    colour = GREEN if total and updated == total else ORANGE if updated else "#64748b"
    picture = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(picture)
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 7, fill=colour)
    inset = max(scale, big // 10)
    draw.rounded_rectangle((inset, inset, big - 1 - inset, big - 1 - inset), radius=big // 10, fill=BG)
    text = str(updated)
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, int(big * (0.78 if len(text) == 1 else 0.58)))
            break
        except OSError:
            continue
    if font is None:
        return None
    draw.text((big / 2, big / 2 + big * 0.02), text, fill="white", font=font, anchor="mm")
    small = picture.resize((size, size), Image.LANCZOS)
    buffer = BytesIO()
    small.save(buffer, format="PNG")
    return buffer.getvalue()


def make_badge(updated: int, total: int, size: int, master=None) -> tk.PhotoImage:
    """Quadratino come immagine tkinter: nitido con Pillow, altrimenti cifre disegnate a pixel."""
    png = smooth_badge_png(updated, total, size)
    if png:
        import base64
        image = tk.PhotoImage(master=master, data=base64.b64encode(png).decode("ascii"), format="png")
        image.png_bytes = png  # serve a write_ico
        return image
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
        if getattr(image, "png_bytes", None):
            blobs.append((image.width(), image.png_bytes))
            continue
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


class Tooltip:
    """Fumetto che compare quando il mouse si ferma su un widget e spiega cosa fa.
    text: testo fisso oppure funzione che lo restituisce (per i pulsanti che cambiano scritta)."""

    def __init__(self, widget: tk.Widget, text, delay: int = 500) -> None:
        self.widget, self.text, self.delay = widget, text, delay
        self.tip: tk.Toplevel | None = None
        self.pending = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _event=None) -> None:
        self.hide()
        self.pending = self.widget.after(self.delay, self.show)

    def show(self) -> None:
        self.pending = None
        text = self.text() if callable(self.text) else self.text
        if not text or self.tip is not None:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)  # la finestra del Monitor è sempre in primo piano
        tk.Label(tip, text=text, justify="left", wraplength=260, font=("Segoe UI", 9),
                 bg="#fef9c3", fg="#1f2937", relief="solid", borderwidth=1, padx=8, pady=5).pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        # dentro lo schermo: se non c'è spazio sotto o a destra va sopra / più a sinistra
        if x + tip.winfo_width() > tip.winfo_screenwidth():
            x = tip.winfo_screenwidth() - tip.winfo_width() - 4
        if y + tip.winfo_height() > tip.winfo_screenheight() - 40:
            y = self.widget.winfo_rooty() - tip.winfo_height() - 4
        tip.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.tip = tip

    def hide(self, _event=None) -> None:
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


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
        self.buttons_frame = buttons  # la sua larghezza decide dove va a capo la scritta dell'esito
        # pulsanti su tre righe (3 + 3 + 3), tutti larghi uguale: la finestra resta stretta
        layout = (("Controlla", self.check, 0, 0), ("Ricontrolla", self.recheck, 0, 1),
                  ("Registro", self.open_log, 0, 2), ("Sito", self.open_site, 1, 0),
                  ("Accessi", self.open_accesses, 1, 1), ("Pianifica", self.toggle_task, 1, 2),
                  ("Panorama", self.overview, 2, 0), ("Pubblica", self.publish, 2, 1),
                  ("FB", self.open_facebook, 2, 2))
        # pulsanti che avviano un giro di Menu.py: spenti (grigi, non cliccabili) mentre un controllo
        # è in corso, perché un secondo giro dovrebbe solo aspettare che finisca il primo
        self.run_buttons: list[tk.Button] = []
        for text, action, row, column in layout:
            button = tk.Button(buttons, text=text, command=action, font=("Segoe UI", 9), bg="#1e293b", fg=FG,
                               activebackground="#334155", activeforeground=FG, relief="flat", width=11,
                               disabledforeground="#475569")
            if action in (self.check, self.recheck, self.overview, self.publish):
                self.run_buttons.append(button)
            button.grid(row=row, column=column, padx=3, pady=3, sticky="we")
            Tooltip(button, self.button_help(action))
            if action == self.toggle_task:
                # il pulsante che cambia scritta ("Pianifica" / "Disabilita"); prima era "l'ultimo
                # pulsante creato" e con Panorama la scritta finiva sul pulsante sbagliato
                self.toggle_button = button
        self.message, self.message_until = "", datetime.min
        self.querying = False

        self.own_mtime = Path(__file__).stat().st_mtime
        self.next_run: datetime | None = None
        self.task_state = ""
        self.last_query = datetime.min
        # click di oggi per rosticceria (dal registro Google, ogni minuto in un thread)
        self.clicks: dict[str, int] | None = None
        self.clicks_at = datetime.min
        self.clicks_busy = False
        self.order_ids: list[str] = []  # ordine delle righe come appaiono adesso
        self.frozen_order: list[str] | None = None  # durante un giro: l'ordine con cui è partito
        self.dots: dict[str, tuple[tk.Label, str]] = {}  # pallina e suo colore normale, per locale
        self.blink_on = False
        self.tick()
        self.blink()

    # --- aggiornamento --------------------------------------------------------------------------
    def tick(self) -> None:
        now = datetime.now()
        # Monitor.py aggiornato su disco: la finestra si riapre da sola con la versione nuova
        try:
            if Path(__file__).stat().st_mtime != self.own_mtime:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=ROOT)
                self.close()
                return
        except OSError:
            pass
        config = read_json(CONFIG, {})
        settings = config.get("impostazioni", {})
        # chiede a Windows ogni minuto, oppure subito dopo che l'esecuzione prevista è passata
        if not self.querying and ((now - self.last_query).total_seconds() > 60
                                  or (self.next_run and now > self.next_run + timedelta(seconds=5))):
            self.querying = True  # domanda a Windows in un thread: la finestra non si blocca
            self.last_query = now
            threading.Thread(target=self.query_task, daemon=True).start()
        if not self.clicks_busy and (now - self.clicks_at).total_seconds() > 60:
            self.clicks_busy, self.clicks_at = True, now
            threading.Thread(target=self.query_clicks, args=(settings.get("registro_click_url", ""),),
                             daemon=True).start()
        active = self.is_active()
        self.toggle_button.config(text="Disabilita" if active else "Pianifica")

        running = check_running()
        for button in self.run_buttons:
            state = "disabled" if running else "normal"
            if button.cget("state") != state:
                button.config(state=state, bg="#111827" if running else "#1e293b",
                              cursor="arrow" if running else "hand2")
        if running:
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
            note = "" if active else "\norari diversi da locali.json: premi Pianifica"
            self.subtitle.config(text=f"al prossimo controllo (ore {target:%H:%M}){note}")

        self.show_shops(config)
        when, outcome = last_outcome()
        colour = RED if outcome.startswith("errore") else GREEN if outcome.startswith("novità") else MUTED
        text = f"Ultimo giro {when}: {outcome}" if when else outcome
        if self.message and now < self.message_until:
            text += f"\n{self.message}"
        # l'esito va a capo alla larghezza delle righe dei locali / dei pulsanti: un testo lungo
        # (es. "novità: A, B, C...") non allarga più la finestra
        wrap = max(self.rows.winfo_reqwidth(), self.buttons_frame.winfo_reqwidth()) - 12
        self.outcome.config(text=text, fg=colour, wraplength=max(200, wrap))
        self.after(1000, self.tick)

    def blink(self) -> None:
        """Durante un controllo la pallina del locale in esame lampeggia giallo brillante;
        le altre restano verdi/arancioni/rosse."""
        current = ""
        if IN_PROGRESS.exists() and check_running():
            current = read_json(IN_PROGRESS, {}).get("id", "")
        self.blink_on = not self.blink_on
        for shop_id, (label, colour) in self.dots.items():
            wanted = (YELLOW if self.blink_on else BG) if shop_id == current else colour
            try:
                if label.cget("fg") != wanted:
                    label.config(fg=wanted)
            except tk.TclError:
                pass  # riga appena ridisegnata
        self.after(400, self.blink)

    def query_clicks(self, log_url: str) -> None:
        """Click di oggi per ogni rosticceria, letti dal registro Google (?azione=statistiche)."""
        import urllib.request
        try:
            if log_url:
                with urllib.request.urlopen(f"{log_url}?azione=statistiche&t={int(time.time())}", timeout=30) as answer:
                    data = json.loads(answer.read().decode("utf-8"))
                rows = data.get("righe", {}) if data.get("data") == date.today().isoformat() else {}
                self.clicks = {name: int(values.get("oggi", 0)) for name, values in rows.items()}
        except Exception:
            pass  # rete assente: resta l'ultimo valore letto
        finally:
            self.clicks_busy = False

    def show_shops(self, config: dict) -> None:
        results = {item.get("id"): item for item in read_json(STATE, {}).get("results", [])}
        today = date.today().isoformat()
        signature = (today, json.dumps(config.get("locali", [])), json.dumps(results, sort_keys=True),
                     json.dumps(self.clicks, sort_keys=True), check_running())
        if signature == getattr(self, "_signature", None):
            return  # niente di nuovo: non ridisegno (evita lo sfarfallio)
        self._signature = signature
        for child in self.rows.winfo_children():
            child.destroy()
        updated = 0
        shops = config.get("locali", [])

        def order(shop: dict) -> tuple[bool, str]:
            # come sul sito: prima quelle "oggi" (verdi), poi le altre; in ciascun gruppo in
            # ordine alfabetico
            published = results.get(shop.get("id"), {}).get("menu_date") == today
            name = unicodedata.normalize("NFD", shop.get("nome", ""))
            return (not published, "".join(ch for ch in name if not unicodedata.combining(ch)).casefold())

        head = tk.Frame(self.rows, bg=BG)
        head.pack(fill="x")
        for text, width, anchor in (("", 2, "w"), ("Rosticceria", 20, "w"), ("Menu", 7, "w"), ("Visite", 6, "e")):
            tk.Label(head, text=text, fg=MUTED, bg=BG, font=("Segoe UI", 8), width=width, anchor=anchor).pack(side="left")
        ordered = sorted(shops, key=order)
        if check_running():
            # giro in corso: le righe restano nell'ordine con cui il giro è partito (è quello che
            # Menu.py sta seguendo), anche se nel frattempo un locale diventa verde
            if self.frozen_order is None:
                # ordine scritto da un pulsante del Monitor appena premuto; per i giri automatici
                # (file vecchio) vale l'ordine attuale, che è quello che segue Menu.py
                try:
                    fresh = time.time() - ORDER_FILE.stat().st_mtime < 300
                except OSError:
                    fresh = False
                saved = read_json(ORDER_FILE, {}).get("ordine") if fresh else None
                self.frozen_order = saved or [item.get("id") for item in ordered]
            place = {key: index for index, key in enumerate(self.frozen_order)}
            ordered = sorted(ordered, key=lambda item: place.get(item.get("id"), len(place)))
        else:
            self.frozen_order = None
        self.order_ids = [item.get("id", "") for item in ordered]
        self.dots = {}
        for shop in ordered:
            result = results.get(shop.get("id"), {})
            day = result.get("menu_date", "")
            if day == today:
                updated += 1
                mark, colour, info = "●", GREEN, "oggi"
            elif day:
                mark, colour, info = "●", ORANGE, "/".join(reversed(day.split("-")[1:]))
            else:
                mark, colour, info = "●", RED, "nessuno"
            row = tk.Frame(self.rows, bg=BG, cursor="hand2")
            row.pack(fill="x")
            dot = tk.Label(row, text=mark, fg=colour, bg=BG, font=("Segoe UI", 11))
            dot.pack(side="left")
            self.dots[shop.get("id", "")] = (dot, colour)
            tk.Label(row, text=shop.get("nome", "?"), fg=FG, bg=BG, font=("Segoe UI", 10), width=20,
                     anchor="w").pack(side="left")
            tk.Label(row, text=info, fg=colour, bg=BG, font=("Segoe UI", 10), width=7, anchor="w").pack(side="left")
            # terza colonna: click di oggi sulla scheda (anche con i nomi usati prima di un cambio di nome)
            if self.clicks is None:
                seen = "–"
            else:
                seen = str(sum(self.clicks.get(name, 0) for name in [shop.get("nome", "")] + shop.get("nomi_precedenti", [])))
            tk.Label(row, text=seen, fg=FG if seen not in ("0", "–") else MUTED, bg=BG, font=("Segoe UI", 10, "bold"),
                     width=5, anchor="e").pack(side="left")
            # clic su una rosticceria = SEGNALAZIONE "il suo menu è probabilmente sbagliato": il menu di
            # oggi viene scartato (e non più riusato) e il locale viene ricontrollato a fondo;
            # le altre rosticcerie restano invariate
            for widget in (row, *row.winfo_children()):
                widget.configure(cursor="hand2")
                widget.bind("<Button-1>", lambda _event, shop_id=shop.get("id", ""): self.run_now("--solo", shop_id))
        self.summary.config(text=f"Aggiornate oggi: {updated} su {len(shops)}")
        self.set_icon(updated, len(shops))

    def is_active(self) -> bool:
        """Attività attiva e con gli orari di locali.json (es. ogni 30 minuti a partire dalle 08:00:
        08:00, 08:30 ...). Un'attività con orari diversi va ricreata con Pianifica (lo fa da sola
        anche Menu.py al primo giro automatico dopo il cambio)."""
        running = self.task_state.lower() in ("ready", "running", "queued")
        dalle, _, every = plan(read_json(CONFIG, {}).get("impostazioni", {}))
        start = int(dalle[:2]) * 60 + int(dalle[3:5])
        aligned = self.next_run is None or (self.next_run.hour * 60 + self.next_run.minute - start) % every == 0
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
                ico = ICON_DIR / f"badge2_{updated}_{total}.ico"  # badge2: versione nitida
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
    def button_help(self, action):
        """Testo del fumetto di ogni pulsante (Pianifica/Disabilita cambia secondo lo stato)."""
        def schedule_help() -> str:
            dalle, alle, every = plan(read_json(CONFIG, {}).get("impostazioni", {}))
            if self.is_active():
                return (f"Disabilita i controlli automatici (ora ogni {every} minuti, dalle {dalle} alle {alle}). "
                        "Chiede conferma: il sito non si aggiorna più da solo finché non premi Pianifica.")
            return f"Attiva i controlli automatici: ogni {every} minuti, dalle {dalle} alle {alle}."
        texts = {
            self.check: "Controlla subito online solo le rosticcerie che non hanno ancora il menu di oggi, "
                        "poi aggiorna e pubblica il sito.",
            self.recheck: "Ricontrolla online TUTTE le rosticcerie, anche quelle già aggiornate "
                          "(es. un menu corretto dopo la pubblicazione), poi pubblica il sito.",
            self.open_log: "Apre nel Blocco note il registro dei controlli (cosa è stato letto e deciso per ogni locale).",
            self.open_site: "Apre il sito Menu come amministratore (i tuoi click non vengono contati). "
                            "Se è già aperto lo porta davanti e lo aggiorna.",
            self.open_accesses: "Apre il foglio Google con i click sulle rosticcerie, sull'ultima riga inserita.",
            self.toggle_task: schedule_help,
            self.overview: "Crea e apre un PDF con, per ogni locale, la scheda del sito e la fonte "
                           "(post o storia) come appare adesso. Richiede qualche minuto.",
            self.open_facebook: "Apre nel browser la pagina Facebook \"Menu rosticcerie\" (post del giorno, riassunto delle 11:30).",
            self.publish: "Rifà la pagina con i menu già scaricati e la pubblica subito online, "
                          "senza ricontrollare Facebook, Instagram e i siti.",
        }
        return texts.get(action, "")

    def check(self) -> None:
        """Controlla: come il giro automatico, ma subito e a qualunque ora.
        Le rosticcerie che hanno già il menu di oggi non vengono ricontrollate online."""
        self.run_now("--salta-aggiornati")

    def recheck(self) -> None:
        """Ricontrolla: ricontrolla online tutte le rosticcerie, anche quelle già aggiornate
        (es. per vedere se un menu di oggi è stato corretto)."""
        self.run_now()

    def run_now(self, *options: str) -> None:
        """Lancia subito un giro in una finestra DOS che mostra solo una riga per rosticceria.

        La finestra resta aperta per leggere l'esito e si chiude da sola dopo 60 secondi; prima di
        aprirne una nuova viene chiusa quella del controllo manuale precedente (riconosciuta dal titolo)."""
        if sys.platform != "win32":
            return
        if check_running():
            self.say("Un controllo è già in corso: attendi che finisca")
            return
        # Menu.py controlla i locali nello stesso ordine delle righe visibili qui
        try:
            ORDER_FILE.write_text(json.dumps({"ordine": self.order_ids}, ensure_ascii=False), encoding="utf-8")
            self.frozen_order = list(self.order_ids)
        except OSError:
            pass
        subprocess.run(["taskkill", "/F", "/T", "/FI", f"WINDOWTITLE eq {CONSOLE_TITLE}*"],
                       capture_output=True, creationflags=NO_WINDOW)
        python = Path(sys.executable).with_name("python.exe")  # Monitor gira con pythonw
        exe = str(python) if python.exists() else "python"
        exe = f'"{exe}"' if " " in exe else exe
        command = " ".join([exe, "Menu.py", "--pubblica", *options])
        # riga di comando passata così com'è (una lista verrebbe ri-quotata e cmd non la capirebbe).
        # Finito il controllo la finestra mostra l'esito e si chiude da sola dopo CONSOLE_SECONDS
        # secondi (un tasto qualsiasi la chiude subito): niente finestre DOS dimenticate aperte.
        closing = (f"echo. & echo La finestra si chiude da sola tra {CONSOLE_SECONDS} secondi "
                   f"(un tasto qualsiasi la chiude subito) & timeout /t {CONSOLE_SECONDS} >nul")
        subprocess.Popen(f'cmd /c "title {CONSOLE_TITLE} & {command} & {closing}"', cwd=ROOT,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        if "--solo-html" in options:
            self.say("Pubblicazione avviata (con i menu già scaricati)")
        elif "--solo" in options:
            self.say("Segnalato: controllo approfondito avviato (menu di oggi scartato)")
        else:
            self.say("Controllo avviato" + (" (solo le rosticcerie non aggiornate)" if options else " (tutte)"))

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
            # disattivare ferma i controlli automatici per tutto il giorno: si chiede conferma
            # (un clic per sbaglio su "Disabilita" li aveva fermati senza che ce ne accorgessimo)
            from tkinter import messagebox
            dalle, alle, every = plan(read_json(CONFIG, {}).get("impostazioni", {}))
            if not messagebox.askyesno("Menu", f"Disattivare i controlli automatici (ogni {every} minuti, {dalle}-{alle})?\n\n"
                                               "Il sito non verrà più aggiornato da solo finché non premi Pianifica.",
                                       icon="warning", default="no", parent=self):
                return
            self.say(disable_task())
        else:
            self.say(schedule_task())
        self.task_state = "..."
        self.last_query = datetime.min  # rilegge subito lo stato da Windows

    def open_accesses(self) -> None:
        """Apre il foglio Google "Menu" con i click, posizionato sull'ultima riga inserita.
        Chiede al registro (Apps Script, ?azione=ultima) indirizzo del foglio e numero dell'ultima riga."""
        self.say("Apro il foglio degli accessi…")
        threading.Thread(target=self._open_accesses, daemon=True).start()

    def _open_accesses(self) -> None:
        import urllib.request
        log_url = read_json(CONFIG, {}).get("impostazioni", {}).get("registro_click_url", "")
        saved = read_json(POSITION, {})
        if not log_url:
            self.say("Registro click non configurato (locali.json)")
            return
        try:
            with urllib.request.urlopen(log_url + "?azione=ultima", timeout=30) as answer:
                info = json.loads(answer.read().decode("utf-8"))
            target = f"{info['url']}#gid={info['gid']}&range=A{max(1, int(info['riga']))}"
            saved["foglio_accessi"] = info["url"]
            POSITION.write_text(json.dumps(saved), encoding="utf-8")
        except Exception:
            if not saved.get("foglio_accessi"):
                self.say("Foglio non raggiungibile: aggiorna Registro_click.gs (vedi LEGGIMI)")
                return
            target = saved["foglio_accessi"]  # ultimo indirizzo noto, senza posizionamento
        webbrowser.open(target)

    def overview(self) -> None:
        """Panorama: crea e apre un PDF con, per ogni locale, la scheda del sito e la fonte
        (post Facebook, storia Facebook/Instagram, sito) come appare adesso."""
        if sys.platform != "win32":
            return
        if check_running():
            self.say("Un controllo è in corso: attendi che finisca")
            return
        python = Path(sys.executable).with_name("python.exe")
        exe = str(python) if python.exists() else "python"
        exe = f'"{exe}"' if " " in exe else exe
        closing = (f"echo. & echo La finestra si chiude da sola tra {CONSOLE_SECONDS} secondi "
                   f"(un tasto qualsiasi la chiude subito) & timeout /t {CONSOLE_SECONDS} >nul")
        subprocess.Popen(f'cmd /c "title Menu - panorama & {exe} Menu.py --panorama & {closing}"', cwd=ROOT,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.say("Panorama avviato: tra qualche minuto si apre il PDF")

    def publish(self) -> None:
        """Pubblica: rifà la pagina con i menu già scaricati sul PC (senza andare su Facebook,
        Instagram e siti) e la pubblica subito su GitHub Pages."""
        self.run_now("--solo-html")

    def open_facebook(self) -> None:
        """FB: apre la pagina Facebook "Menu rosticcerie" nel browser predefinito."""
        webbrowser.open(FACEBOOK_URL)

    def open_site(self) -> None:
        """Sito: se il sito è già aperto (scheda "Menu" di Chrome/Edge, o l'app "Menu") lo porta in
        primo piano e lo aggiorna; altrimenti lo apre (con ?v=57: amministratore, i click non
        vengono registrati). La ricerca gira in un thread: la finestra non si blocca."""
        threading.Thread(target=self._open_site, daemon=True).start()

    def _open_site(self) -> None:
        found = ""
        if sys.platform == "win32":
            try:
                script = ROOT / "dati" / "sito.ps1"
                script.parent.mkdir(exist_ok=True)
                if not script.exists() or script.read_text(encoding="utf-8-sig") != SITE_SCRIPT:
                    script.write_text(SITE_SCRIPT, encoding="utf-8-sig")  # con BOM: PowerShell legge bene gli accenti
                done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                      capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
                found = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
            except Exception as exc:
                log_error(f"Sito: ricerca della scheda non riuscita: {exc!r}")
        if found in ("scheda", "app"):
            self.say("Sito già aperto: aggiornato")
        else:
            webbrowser.open(WEB_URL + "?v=57")  # amministratore: i click non vengono registrati

    def close(self) -> None:
        try:
            POSITION.parent.mkdir(exist_ok=True)
            saved = read_json(POSITION, {})
            saved.update({"x": self.winfo_x(), "y": self.winfo_y()})
            POSITION.write_text(json.dumps(saved), encoding="utf-8")
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
