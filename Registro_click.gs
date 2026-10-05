/**
 * Registro_click.gs - riceve i click della pagina Menu e li scrive nel foglio Google "Menu".
 *
 * Installazione (una sola volta, vedi LEGGIMI.txt > "Registro dei click"):
 *  1. Crea un nuovo Foglio Google chiamato "Menu".
 *  2. Estensioni > Apps Script, cancella il contenuto e incolla tutto questo file. Salva.
 *  3. Esegui il deployment > Nuovo deployment > tipo "App web"
 *     Esegui come: Me    -    Chi può accedere: Chiunque    > Esegui il deployment
 *     (al primo avvio Google chiede di autorizzare l'accesso al foglio: consenti).
 *  4. Copia l'URL dell'app web (finisce con /exec) in locali.json > "registro_click_url".
 *
 * Ogni click su una scheda aggiunge una riga: Data | Ora | Rosticceria | Dispositivo | Posizione approssimativa.
 */
function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000); // due click contemporanei non si sovrascrivono
  try {
    var dati = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    var foglio = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    if (foglio.getLastRow() === 0) {
      foglio.appendRow(['Data', 'Ora', 'Rosticceria', 'Dispositivo', 'Posizione approssimativa']);
    }
    var adesso = new Date();
    foglio.appendRow([
      Utilities.formatDate(adesso, 'Europe/Rome', 'yyyy-MM-dd'),
      Utilities.formatDate(adesso, 'Europe/Rome', 'HH:mm:ss'),
      String(dati.rosticceria || '').slice(0, 100),
      String(dati.dispositivo || '').slice(0, 100),
      String(dati.posizione || '').slice(0, 150)
    ]);
    return ContentService.createTextOutput('ok');
  } finally {
    lock.releaseLock();
  }
}

/**
 * GET senza parametri: "Registro click Menu attivo" (controllo che il deployment funzioni).
 * GET ?azione=ultima: indirizzo del foglio e numero dell'ultima riga, usati dal pulsante
 * "Accessi" della finestra di controllo (Monitor.py) per aprire il foglio sull'ultimo click.
 */
function doGet(e) {
  if (e && e.parameter && e.parameter.azione === 'ultima') {
    var file = SpreadsheetApp.getActiveSpreadsheet();
    var foglio = file.getSheets()[0];
    var risposta = { url: file.getUrl(), gid: foglio.getSheetId(), riga: foglio.getLastRow() };
    return ContentService.createTextOutput(JSON.stringify(risposta))
      .setMimeType(ContentService.MimeType.JSON);
  }
  return ContentService.createTextOutput('Registro click Menu attivo');
}
