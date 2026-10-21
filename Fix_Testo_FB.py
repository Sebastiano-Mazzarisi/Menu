# Nome.py: Fix_Testo_FB.py
# Data e ora: 21/10/2026 14:35
# Descrizione: Sostituisce la funzione summary_text in Menu.py per usare solo la didascalia richiesta

with open("Menu.py", "r", encoding="utf-8") as f:
    code = f.read()

# La funzione summary_text modificata con la didascalia semplice
nuova_funzione = '''def summary_text(data: dict[str, Any]) -> str:
    """Didascalia essenziale del post su Facebook."""
    pronti = len(data.get("pronti", []))
    totale = data.get("totale", 13)
    ora = data.get("ora", "11:45")
    day = data["giorno"]
    giorni = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
    data_estesa = f"{giorni[day.weekday()]} {day.day} {mesi[day.month - 1]} {day.year}"
    return f"Clicca sull'immagine per vedere i menu pubblicati alle {ora} di oggi {data_estesa}, {pronti} rosticcerie su {totale} hanno già pubblicato il menu del giorno."
'''

# Sostituisce da "def summary_text" fino a prima di "SHORT_URL"
import re
code_mod = re.sub(
    r'def summary_text\(data: dict\[str, Any\]\) -> str:.*?(?=SHORT_URL =)',
    nuova_funzione + '\n\n',
    code,
    flags=re.DOTALL
)

with open("Menu.py", "w", encoding="utf-8") as f:
    f.write(code_mod)

print("Funzione summary_text aggiornata con la didascalia esatta!")