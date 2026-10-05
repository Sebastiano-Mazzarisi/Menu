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
 *
 * Per rispondere subito, i conteggi (oggi / mese / anno / tutto) sono tenuti già pronti nelle
 * "Proprietà dello script" e aggiornati a ogni click: non serve rileggere tutto il foglio.
 * Se il foglio viene modificato a mano (righe cancellate o aggiunte), il numero di righe non
 * corrisponde più e i conteggi vengono ricalcolati da capo automaticamente.
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
    aggiungiAiConti(String(dati.rosticceria || '').slice(0, 100), foglio);
    return ContentService.createTextOutput('ok');
  } finally {
    lock.releaseLock();
  }
}

/**
 * GET senza parametri: "Registro click Menu attivo" (controllo che il deployment funzioni).
 * GET ?azione=statistiche: click per rosticceria di oggi, del mese, dell'anno e di sempre
 *   (contatori e finestra "Info" della pagina con ?v=57).
 * GET ?azione=oggi: solo i click di oggi (compatibilità con le pagine vecchie).
 * GET ?azione=ricalcola: ricalcola i conteggi rileggendo tutto il foglio.
 * GET ?azione=ultima: indirizzo del foglio e numero dell'ultima riga, usati dal pulsante
 * "Accessi" della finestra di controllo (Monitor.py) per aprire il foglio sull'ultimo click.
 */
function doGet(e) {
  var azione = (e && e.parameter && e.parameter.azione) || '';
  if (azione === 'statistiche' || azione === 'ricalcola') {
    return json(statistiche(azione === 'ricalcola'));
  }
  if (azione === 'oggi') {
    var s = statistiche(false), conti = {};
    for (var nome in s.righe) if (s.righe[nome].oggi) conti[nome] = s.righe[nome].oggi;
    return json({ data: s.data, conti: conti, totale: s.totali.oggi });
  }
  if (azione === 'ultima') {
    var file = SpreadsheetApp.getActiveSpreadsheet();
    var foglio = file.getSheets()[0];
    return json({ url: file.getUrl(), gid: foglio.getSheetId(), riga: foglio.getLastRow() });
  }
  return ContentService.createTextOutput('Registro click Menu attivo');
}

function json(oggetto) {
  return ContentService.createTextOutput(JSON.stringify(oggetto)).setMimeType(ContentService.MimeType.JSON);
}

var PERIODI = ['oggi', 'mese', 'anno', 'tutto'];

/** { data, righe: { "Fantasia": {oggi, mese, anno, tutto}, ... }, totali: {oggi, mese, anno, tutto} } */
function statistiche(daCapo) {
  // senza aprire il foglio (più veloce): i conteggi salvati sono già aggiornati da ogni click;
  // se il foglio è stato modificato a mano se ne accorge il click successivo (o ?azione=ricalcola)
  var c = daCapo ? null : contiSalvati();
  if (!c) c = ricalcola(SpreadsheetApp.getActiveSpreadsheet().getSheets()[0]);
  var totali = { oggi: 0, mese: 0, anno: 0, tutto: 0 };
  for (var nome in c.righe) PERIODI.forEach(function (k) { totali[k] += c.righe[nome][k]; });
  return { data: c.data, righe: c.righe, totali: totali };
}

/** Conteggi salvati, aggiornati al giorno di oggi (a mezzanotte "oggi" riparte da 0, ecc.), o null. */
function contiSalvati() {
  var testo = PropertiesService.getScriptProperties().getProperty('conti');
  if (!testo) return null;
  var c = JSON.parse(testo);
  var oggi = Utilities.formatDate(new Date(), 'Europe/Rome', 'yyyy-MM-dd');
  if (c.data !== oggi) {
    for (var nome in c.righe) {
      var r = c.righe[nome];
      r.oggi = 0;
      if (c.data.slice(0, 7) !== oggi.slice(0, 7)) r.mese = 0;
      if (c.data.slice(0, 4) !== oggi.slice(0, 4)) r.anno = 0;
    }
    c.data = oggi;
  }
  return c;
}

/** Rilegge tutto il foglio e salva i conteggi. */
function ricalcola(foglio) {
  var tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  var oggi = Utilities.formatDate(new Date(), 'Europe/Rome', 'yyyy-MM-dd');
  var c = { data: oggi, ultima: foglio.getLastRow(), righe: {} };
  if (c.ultima > 1) {
    var dati = foglio.getRange(2, 1, c.ultima - 1, 3).getValues();
    for (var i = 0; i < dati.length; i++) {
      var nome = String(dati[i][2] || '');
      if (!nome || nome === 'Prova di funzionamento') continue;
      var giorno = giornoDi(dati[i][0], tz);
      var r = c.righe[nome] || (c.righe[nome] = { oggi: 0, mese: 0, anno: 0, tutto: 0 });
      if (giorno === oggi) r.oggi++;
      if (giorno.slice(0, 7) === oggi.slice(0, 7)) r.mese++;
      if (giorno.slice(0, 4) === oggi.slice(0, 4)) r.anno++;
      r.tutto++;
    }
  }
  salvaConti(c);
  return c;
}

/** Chiamata da doPost (dentro il lock) subito dopo aver scritto la riga: +1 a quella rosticceria. */
function aggiungiAiConti(nome, foglio) {
  var c = contiSalvati();
  var ultima = foglio.getLastRow();
  if (!c || c.ultima !== ultima - 1) return ricalcola(foglio); // foglio cambiato a mano: da capo
  if (nome && nome !== 'Prova di funzionamento') {
    var r = c.righe[nome] || (c.righe[nome] = { oggi: 0, mese: 0, anno: 0, tutto: 0 });
    PERIODI.forEach(function (k) { r[k]++; });
  }
  c.ultima = ultima;
  salvaConti(c);
  return c;
}

function salvaConti(c) {
  PropertiesService.getScriptProperties().setProperty('conti', JSON.stringify(c));
}

/**
 * Data di una riga come testo "aaaa-mm-gg", qualunque cosa contenga la cella: il foglio converte
 * da solo "2026-10-05" in una data vera (che va letta nel fuso del foglio), oppure resta testo.
 */
function giornoDi(valore, tz) {
  if (valore && typeof valore.getFullYear === 'function') {
    return Utilities.formatDate(valore, tz || 'Europe/Rome', 'yyyy-MM-dd');
  }
  var t = String(valore || '').trim();
  var m = t.match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{4})/); // gg/mm/aaaa
  if (m) return m[3] + '-' + ('0' + m[2]).slice(-2) + '-' + ('0' + m[1]).slice(-2);
  return t.slice(0, 10);
}
