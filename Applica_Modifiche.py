# Nome.py: Applica_Modifiche.py
# Data e ora: 21/10/2026 14:05
# Descrizione: Modifica Menu.py per rendere automatica la generazione dell'immagine con bordo arancione e banner cliccabile
# File di input: Menu.py
# File di output: Menu.py

import os
import shutil
import re

MENU_PATH = "Menu.py"
BACKUP_PATH = os.path.join("backup", "Menu_2026-10-21_prima-di-banner-automatico.py")

def main():
    if not os.path.exists(MENU_PATH):
        print(f"Errore: {MENU_PATH} non trovato!")
        return

    os.makedirs("backup", exist_ok=True)
    shutil.copy2(MENU_PATH, BACKUP_PATH)
    print(f"Backup di sicurezza salvato in: {BACKUP_PATH}")

    with open(MENU_PATH, "r", encoding="utf-8") as f:
        code = f.read()

    # 1. Modifica CSS in summary_html: cornice arancione e logo nitido al 100% per i locali 'no' (in attesa)
    old_css_no = ".cd.no{background:#ebe4d8;box-shadow:none;border-color:transparent}\n.cd.no img,.cd.no .nologo{opacity:.45;filter:grayscale(1)}"
    new_css_no = ".cd.no{background:#fff;box-shadow:none;border:3px solid #e67e22}\n.cd.no img,.cd.no .nologo{opacity:1;filter:none}"
    
    if old_css_no in code:
        code = code.replace(old_css_no, new_css_no)
        print("CSS summary_html: applicata cornice arancione e logo visibile al 100%.")
    else:
        # Sostituzione con regex in caso di lievi variazioni di spaziature
        code = re.sub(
            r'\.cd\.no\{background:#ebe4d8;box-shadow:none;border-color:transparent\}[\r\n]+\.cd\.no img,\.cd\.no \.nologo\{opacity:\.45;filter:grayscale\(1\)\}',
            new_css_no,
            code
        )
        print("CSS summary_html aggiornato tramite espressione regolare.")

    # 2. Inserimento del banner come primo elemento del <main id="main"> in PAGE_TEMPLATE
    banner_markup = r'''<main id="main">
<!-- Banner primo elemento: riassunto del giorno cliccabile per aprire menu.pdf -->
<figure id="banner-riassunto" style="text-align:center;margin:12px auto 18px;max-width:520px;padding:0 8px;">
  <a href="https://menurosticcerie.github.io/menu.pdf" target="_blank" rel="noopener noreferrer" title="Clicca per aprire il PDF di tutti i menu">
    <img src="riassunto-oggi.jpg?v=@@RV@@" alt="Stato menu del giorno" style="width:100%;height:auto;border-radius:14px;cursor:pointer;display:block;box-shadow:0 4px 14px rgba(0,0,0,0.12);" />
  </a>
  <figcaption style="margin-top:8px;font-size:0.92rem;line-height:1.4;color:var(--ink);text-align:center;">
    Clicca sull'immagine per vedere i menu pubblicati alle @@ORA_RIASSUNTO@@ di oggi @@DATA_ESTESA@@, @@PRONTI_COUNT@@ rosticcerie su @@TOTALI_COUNT@@ hanno già pubblicato il menu del giorno.
  </figcaption>
</figure>'''

    if 'id="banner-riassunto"' not in code:
        code = code.replace('<main id="main">', banner_markup, 1)
        print("Banner inserito in cima al tag <main> dentro PAGE_TEMPLATE.")

    # 3. Aggiornamento in generate_html per rigenerare sempre riassunto-oggi.jpg e valorizzare i segnaposto
    target_pattern = r'(\s+document = PAGE_TEMPLATE)'
    injection = '''
    # Generazione e copia automatica del riassunto del giorno
    config_current = read_json(CONFIG, {"impostazioni": settings, "locali": shops})
    summary_res = make_summary(config_current)
    rv = "0"
    if summary_res:
        img_src, _ = summary_res
        dst_img = ROOT / "riassunto-oggi.jpg"
        try:
            shutil.copy2(img_src, dst_img)
            rv = file_hash(dst_img)[:8]
        except Exception:
            pass

    s_data = summary_data(config_current)
    pronti_cnt = str(len(s_data.get("pronti", [])))
    totali_cnt = str(s_data.get("totale", len(shops)))
    ora_r = stamp.get("ora", datetime.now().strftime("%H:%M"))
    data_ext = f"{days[today.weekday()].lower()} {today.day} {list(MONTHS)[today.month - 1]} {today.year}"

    document = PAGE_TEMPLATE
    document = document.replace("@@RV@@", rv)
    document = document.replace("@@ORA_RIASSUNTO@@", ora_r)
    document = document.replace("@@DATA_ESTESA@@", data_ext)
    document = document.replace("@@PRONTI_COUNT@@", pronti_cnt)
    document = document.replace("@@TOTALI_COUNT@@", totali_cnt)
'''

    if '@@PRONTI_COUNT@@' not in code:
        code = re.sub(target_pattern, injection, code, count=1)
        print("Hook di aggiornamento automatico inserito in generate_html.")

    with open(MENU_PATH, "w", encoding="utf-8") as f:
        f.write(code)

    print("\nMenu.py aggiornato con successo!")

if __name__ == "__main__":
    main()