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


class Monitor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Menu")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        if sys.platform == "win32":
            self.attributes("-toolwindow", True)  # barra del titolo piccola, niente icona in taskbar
        icon = ROOT / "icone" / "favicon.png"
        if icon.exists():
            try:
                self.iconphoto(True, tk.PhotoImage(file=str(icon)))
            except tk.TclError:
                pass
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

    def is_active(self) -> bool:
        """Attività attiva e con orari fissi (:00, :15, :30, :45). Una vecchia attività creata
        a un'ora qualsiasi va ricreata con Pianifica."""
        running = self.task_state.lower() in ("ready", "running", "queued")
        aligned = self.next_run is None or self.next_run.minute % 15 == 0
        return running and aligned

    def say(self, message: str) -> None:
        """Messaggio temporaneo (10 secondi) sotto l'esito dell'ultimo giro."""
        self.message, self.message_until = message, datetime.now() + timedelta(seconds=10)

    def query_task(self) -> None:
        self.next_run, self.task_state = task_info()
        self.querying = False

    # --- pulsanti -------------------------------------------------------------------------------
    def run_now(self) -> None:
        """Lancia subito un giro completo nella sua finestra (come Avvia.bat)."""
        bat = ROOT / "Avvia.bat"
        if bat.exists() and sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", str(bat)], cwd=ROOT, creationflags=NO_WINDOW)

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
        webbrowser.open(WEB_URL)

    def close(self) -> None:
        try:
            POSITION.parent.mkdir(exist_ok=True)
            POSITION.write_text(json.dumps({"x": self.winfo_x(), "y": self.winfo_y()}), encoding="utf-8")
        finally:
            self.destroy()


if __name__ == "__main__":
    Monitor().mainloop()
