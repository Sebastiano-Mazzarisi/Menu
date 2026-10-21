with open("Menu.py", "r", encoding="utf-8") as f:
    testo = f.read()

# Le righe esatte con le DOPPIE graffe {{ }} di summary_html
vecchio = """.cd.no{{background:#ebe4d8;box-shadow:none;border-color:transparent}}
.cd.no img,.cd.no .nologo{{opacity:.45;filter:grayscale(1)}}"""

nuovo = """.cd.no{{background:#fff;box-shadow:none;border:3px solid #e67e22}}
.cd.no img,.cd.no .nologo{{opacity:1;filter:none}}"""

if vecchio in testo:
    testo = testo.replace(vecchio, nuovo)
    print("Sostituito con successo!")
elif vecchio.replace("\n", "\r\n") in testo:
    testo = testo.replace(vecchio.replace("\n", "\r\n"), nuovo.replace("\n", "\r\n"))
    print("Sostituito con successo (CRLF)!")
else:
    import re
    testo = re.sub(
        r'\.cd\.no\{\{background:[^;]+;box-shadow:none;border-color:[^}]+\}\}\s*\.cd\.no img,\.cd\.no \.nologo\{\{[^}]+\}\}',
        nuovo,
        testo
    )
    print("Sostituito tramite regex!")

with open("Menu.py", "w", encoding="utf-8") as f:
    f.write(testo)