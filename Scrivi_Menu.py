# Nome.py: Scrivi_Menu.py
# Data e ora: 21/10/2026 14:15
# Descrizione: Applica le modifiche mirate a Menu.py per il riassunto con bordo arancione e banner in cima

import re

with open("Menu.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Regola CSS per i locali in attesa: cornice arancione e logo nitido al 100%
css_vecchio = """.cd.no{background:#ebe4d8;box-shadow:none;border-color:transparent}
.cd.no img,.cd.no .nologo{opacity:.45;filter:grayscale(1)}"""

css_nuovo = """.cd.no{background:#fff;box-shadow:none;border:3px solid #e67e22}
.cd.no img,.cd.no .nologo{opacity:1;filter:none}"""

if css_vecchio in code:
    code = code.replace(css_vecchio, css_nuovo)
elif css_vecchio.replace("\n", "\r\n") in code:
    code = code.replace(css_vecchio.replace("\n", "\r\n"), css_nuovo.replace("\n", "\r\n"))
else:
    code = re.sub(
        r'\.cd\.no\{background:#ebe4d8;box-shadow:none;border-color:transparent\}\s*\.cd\.no img,\.cd\.no \.nologo\{opacity:\.45;filter:grayscale\(1\)\}',
        css_nuovo,
        code
    )

# 2. Inserimento del banner con link al PDF in cima a <main>
banner_html = """<main id="main">
<!-- Banner primo elemento: riassunto del giorno cliccabile per aprire menu.pdf -->
<figure id="banner-riassunto" style="text-align:center;margin:12px auto 18px;max-width:520px;padding:0 8px;">
  <a href="https://menurosticcerie.github.io/menu.pdf" target="_blank" rel="noopener noreferrer" title="Clicca per aprire il PDF di tutti i menu">
    <img src="riassunto-oggi.jpg?v=@@RV@@" alt="Stato menu del giorno" style="width:100%;height:auto;border-radius:14px;cursor:pointer;display:block;box-shadow:0 4px 14px rgba(0,0,0,0.12);" />
  </a>
  <figcaption style="margin-top:8px;font-size:0.92rem;line-height:1.4;color:var(--ink);text-align:center;">
    Clicca sull'immagine per vedere i menu pubblicati alle @@ORA_RIASSUNTO@@ di oggi @@DATA_ESTESA@@, @@PRONTI_COUNT@@ rosticcerie su @@TOTALI_COUNT@@ hanno già pubblicato il menu del giorno.
  </figcaption>
</figure>"""

if 'id="banner-riassunto"' not in code:
    code = code.replace('<main id="main">', banner_html, 1)

# 3. Hook in generate_html per generare l'immagine e valorizzare i testi dinamici
hook_vecchio = "    document = PAGE_TEMPLATE"
hook_nuovo = """    # Generazione e copia automatica del riassunto del giorno
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
    document = document.replace("@@TOTALI_COUNT@@", totali_cnt)"""

if '@@PRONTI_COUNT@@' not in code and hook_vecchio in code:
    code = code.replace(hook_vecchio, hook_nuovo, 1)

with open("Menu.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Menu.py modificato con successo!")