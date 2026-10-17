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
 * Ogni VISITA alla pagina (apertura del sito, anche senza aprire schede) aggiunge una riga nel
 * foglio "Visite" dello stesso file: Data | Ora | Pagina | Dispositivo | Posizione approssimativa.
 * Il foglio "Visite" viene creato da solo (come ultimo foglio: i click restano nel primo).
 *
 * Per rispondere subito, i conteggi (oggi / mese / anno / tutto) sono tenuti già pronti nelle
 * "Proprietà dello script" e aggiornati a ogni click: non serve rileggere tutto il foglio.
 * Se il foglio viene modificato a mano (righe cancellate, celle corrette, foglio svuotato) i
 * conteggi vengono ricalcolati da capo automaticamente alla richiesta successiva.
 */
function doPost(e) {
  return registra(JSON.parse((e && e.postData && e.postData.contents) || '{}'));
}

/** Scrive una visita (dati.tipo === 'visita') o un click. Usata da doPost e da GET ?azione=registra&d={...}
 *  (il metodo "immagine" della pagina, che funziona anche nei browser interni di Facebook e Instagram). */
function registra(dati) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000); // due click contemporanei non si sovrascrivono
  try {
    if (dati.tipo === 'visita') {
      registraVisita(dati);
      aggiornaContatoreSicuro();
      return ContentService.createTextOutput('ok');
    }
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
    aggiornaContatoreSicuro();
    return ContentService.createTextOutput('ok');
  } finally {
    lock.releaseLock();
  }
}

/**
 * GET senza parametri: "Registro click Menu attivo" (controllo che il deployment funzioni).
 * GET ?azione=statistiche: click per rosticceria di oggi, del mese, dell'anno e di sempre
 *   (contatori e finestra "Info" della pagina con ?v=57), più la distribuzione delle visite
 *   per giorno della settimana e per ora del giorno (i due grafici sotto la tabella di Info).
 * GET ?azione=oggi: solo i click di oggi (compatibilità con le pagine vecchie).
 * GET ?azione=ricalcola: ricalcola i conteggi rileggendo tutto il foglio.
 * GET ?azione=registra&d={json}: come doPost (visita o click), per il metodo "immagine" della pagina.
 * GET ?azione=sistema: toglie le righe di prova (Prova di funzionamento, PROVA..., DEBUG...) e aggiorna
 *   la scheda "Contatore" (visite e click di oggi, del mese, totali e giorno per giorno).
 * GET ?azione=ultima: indirizzo del foglio e numero dell'ultima riga, usati dal pulsante
 * "Accessi" della finestra di controllo (Monitor.py) per aprire il foglio sull'ultimo click.
 */
function doGet(e) {
  var azione = (e && e.parameter && e.parameter.azione) || '';
  if (azione === 'sistema') {  // una volta: toglie le righe di prova e (ri)crea la scheda Contatore
    var tolte = pulisciProve();
    aggiornaContatore();
    return ContentService.createTextOutput('ok: tolte ' + tolte + ' righe di prova, Contatore aggiornato');
  }
  if (azione === 'registra') {
    var dati = {};
    try { dati = JSON.parse(e.parameter.d || '{}'); } catch (err) { return ContentService.createTextOutput('dati non validi'); }
    return registra(dati);
  }
  if (azione === 'statistiche' || azione === 'ricalcola') {
    var s = statistiche(azione === 'ricalcola');
    s.visite = statisticheVisite();
    var d = distribuzioneVisite();
    s.settimana = d.settimana;
    s.ore = d.ore;
    return json(s);
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
  return ContentService.createTextOutput('Registro click Menu attivo (con visite)');
}

function json(oggetto) {
  return ContentService.createTextOutput(JSON.stringify(oggetto)).setMimeType(ContentService.MimeType.JSON);
}

var PERIODI = ['oggi', 'mese', 'anno', 'tutto'];

/** { data, righe: { "Fantasia": {oggi, mese, anno, tutto}, ... }, totali: {oggi, mese, anno, tutto} } */
function statistiche(daCapo) {
  // i conteggi salvati sono già aggiornati da ogni click; se il numero di righe del foglio non
  // corrisponde (righe cancellate, foglio svuotato) o il foglio è stato modificato a mano
  // (onEdit qui sotto cancella i conteggi salvati), vengono ricalcolati da capo
  var foglio = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  var c = daCapo ? null : contiSalvati();
  if (!c || c.ultima !== foglio.getLastRow()) c = ricalcola(foglio);
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

/** Ogni modifica fatta a mano nel foglio (cancellare, correggere, svuotare) azzera i conteggi
 *  salvati: alla prossima richiesta vengono ricalcolati dal foglio. Scatta da solo, non va eseguita. */
function onEdit(e) {
  PropertiesService.getScriptProperties().deleteProperty('conti');
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

/** Foglio "Visite" (creato al primo uso, in fondo: il primo foglio resta quello dei click). */
function foglioVisite() {
  var file = SpreadsheetApp.getActiveSpreadsheet();
  var foglio = file.getSheetByName('Visite');
  if (!foglio) {
    foglio = file.insertSheet('Visite', file.getSheets().length);
    foglio.appendRow(['Data', 'Ora', 'Pagina', 'Dispositivo', 'Posizione approssimativa']);
  }
  return foglio;
}

function registraVisita(dati) {
  var adesso = new Date();
  foglioVisite().appendRow([
    Utilities.formatDate(adesso, 'Europe/Rome', 'yyyy-MM-dd'),
    Utilities.formatDate(adesso, 'Europe/Rome', 'HH:mm:ss'),
    String(dati.pagina || '').slice(0, 60),
    String(dati.dispositivo || '').slice(0, 100),
    String(dati.posizione || '').slice(0, 150)
  ]);
}

/** Visite alla pagina: { oggi, mese, anno, tutto }, contate dal foglio "Visite". */
function statisticheVisite() {
  var foglio = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Visite');
  var v = { oggi: 0, mese: 0, anno: 0, tutto: 0 };
  if (!foglio || foglio.getLastRow() < 2) return v;
  var tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  var oggi = Utilities.formatDate(new Date(), 'Europe/Rome', 'yyyy-MM-dd');
  var date = foglio.getRange(2, 1, foglio.getLastRow() - 1, 1).getValues();
  for (var i = 0; i < date.length; i++) {
    var giorno = giornoDi(date[i][0], tz);
    if (!giorno) continue;
    if (giorno === oggi) v.oggi++;
    if (giorno.slice(0, 7) === oggi.slice(0, 7)) v.mese++;
    if (giorno.slice(0, 4) === oggi.slice(0, 4)) v.anno++;
    v.tutto++;
  }
  return v;
}

/**
 * Visite alla pagina distribuite per giorno della settimana e per ora del giorno, da tutto il
 * foglio "Visite" (se non c'è ancora, dai click del primo foglio):
 *   settimana: [dom, lun, mar, mer, gio, ven, sab]   ore: [0, 1, ..., 23]
 * La pagina le mostra come due istogrammi orizzontali sotto la tabella di "Info".
 */
function distribuzioneVisite() {
  var file = SpreadsheetApp.getActiveSpreadsheet();
  var tz = file.getSpreadsheetTimeZone();
  var foglio = file.getSheetByName('Visite');
  if (!foglio || foglio.getLastRow() < 2) foglio = file.getSheets()[0];
  var settimana = [0, 0, 0, 0, 0, 0, 0], ore = [];
  for (var h = 0; h < 24; h++) ore.push(0);
  if (foglio.getLastRow() < 2) return { settimana: settimana, ore: ore };
  var righe = foglio.getRange(2, 1, foglio.getLastRow() - 1, 2).getValues();
  for (var i = 0; i < righe.length; i++) {
    var giorno = giornoDi(righe[i][0], tz);
    var m = giorno.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) continue;
    settimana[new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getDay()]++;
    var ora = oraDi(righe[i][1], tz);
    if (ora >= 0 && ora < 24) ore[ora]++;
  }
  return { settimana: settimana, ore: ore };
}

/** Ora (0-23) di una cella "Ora": testo "07:45:18" oppure un orario vero del foglio. -1 se illeggibile. */
function oraDi(valore, tz) {
  if (valore && typeof valore.getFullYear === 'function') {
    return Number(Utilities.formatDate(valore, tz || 'Europe/Rome', 'H'));
  }
  var m = String(valore || '').match(/^\s*(\d{1,2})[:.]/);
  return m ? Number(m[1]) : -1;
}


/* ---------------------------------------------------------------------------------------------
 * SCHEDA "CONTATORE": visite al sito e click sulle rosticcerie di oggi, del mese, in totale e
 * giorno per giorno (dal più recente). Valori scritti dallo script (niente formule: funziona con
 * qualunque lingua del foglio), aggiornati a ogni visita o click. Le righe "Prova di funzionamento"
 * non si contano. È l'ultima scheda: il primo foglio resta quello dei click.
 * ------------------------------------------------------------------------------------------- */
function aggiornaContatoreSicuro() {
  try { aggiornaContatore(); } catch (err) { /* il contatore non deve mai impedire la registrazione */ }
}

function giorniDi(foglio, conColonnaNome) {
  var tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  var conti = {};
  if (!foglio || foglio.getLastRow() < 2) return conti;
  var righe = foglio.getRange(2, 1, foglio.getLastRow() - 1, 3).getValues();
  for (var i = 0; i < righe.length; i++) {
    if (conColonnaNome && String(righe[i][2]) === 'Prova di funzionamento') continue;
    var g = giornoDi(righe[i][0], tz);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(g)) continue;
    conti[g] = (conti[g] || 0) + 1;
  }
  return conti;
}

function aggiornaContatore() {
  var file = SpreadsheetApp.getActiveSpreadsheet();
  var visite = giorniDi(file.getSheetByName('Visite'), false);
  var click = giorniDi(file.getSheets()[0], true);
  var oggi = Utilities.formatDate(new Date(), 'Europe/Rome', 'yyyy-MM-dd');
  var somma = function (conti, filtro) {
    var n = 0;
    for (var g in conti) if (filtro(g)) n += conti[g];
    return n;
  };
  var tutti = function () { return true; };
  var delGiorno = function (g) { return g === oggi; };
  var delMese = function (g) { return g.slice(0, 7) === oggi.slice(0, 7); };
  var giorni = Object.keys(visite).concat(Object.keys(click))
    .filter(function (g, i, a) { return a.indexOf(g) === i; }).sort().reverse();
  var mesi = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic'];
  var settimana = ['dom', 'lun', 'mar', 'mer', 'gio', 'ven', 'sab'];
  var bello = function (g) {
    var d = new Date(Number(g.slice(0, 4)), Number(g.slice(5, 7)) - 1, Number(g.slice(8, 10)));
    return settimana[d.getDay()] + ' ' + d.getDate() + ' ' + mesi[d.getMonth()] + ' ' + d.getFullYear();
  };
  var righe = [
    ['Contatore Menu', '', ''],
    ['aggiornato il ' + Utilities.formatDate(new Date(), 'Europe/Rome', 'dd/MM/yyyy HH:mm'), '', ''],
    ['', 'Visite al sito', 'Click sulle rosticcerie'],
    ['Oggi', somma(visite, delGiorno), somma(click, delGiorno)],
    ['Questo mese', somma(visite, delMese), somma(click, delMese)],
    ['Totale', somma(visite, tutti), somma(click, tutti)],
    ['', '', ''],
    ['Giorno', 'Visite', 'Click']
  ];
  giorni.forEach(function (g) { righe.push([bello(g), visite[g] || 0, click[g] || 0]); });
  var foglio = file.getSheetByName('Contatore');
  if (!foglio) {
    foglio = file.insertSheet('Contatore', file.getSheets().length);
  }
  foglio.clearContents();
  foglio.getRange(1, 1, righe.length, 3).setValues(righe);
  foglio.getRange('A1').setFontSize(14).setFontWeight('bold');
  foglio.getRange('A2').setFontColor('#777777');
  foglio.getRange('A3:C3').setFontWeight('bold');
  foglio.getRange('A4:A6').setFontWeight('bold');
  foglio.getRange('B4:C6').setFontSize(13).setHorizontalAlignment('center');
  foglio.getRange('A8:C8').setFontWeight('bold');
  foglio.getRange(9, 2, Math.max(1, righe.length - 8), 2).setHorizontalAlignment('center');
  foglio.setColumnWidth(1, 170);
  foglio.setColumnWidth(2, 120);
  foglio.setColumnWidth(3, 170);
}

/** Toglie dai fogli Click e Visite le righe di prova; restituisce quante. */
function pulisciProve() {
  var file = SpreadsheetApp.getActiveSpreadsheet();
  var tz = file.getSpreadsheetTimeZone();
  var tolte = 0;
  [file.getSheets()[0], file.getSheetByName('Visite')].forEach(function (foglio) {
    if (!foglio || foglio.getLastRow() < 2) return;
    var righe = foglio.getRange(2, 1, foglio.getLastRow() - 1, 4).getValues();
    for (var i = righe.length - 1; i >= 0; i--) {
      var nome = String(righe[i][2]), disp = String(righe[i][3]);
      var ora = righe[i][1] && typeof righe[i][1].getFullYear === 'function'
        ? Utilities.formatDate(righe[i][1], tz, 'HH:mm:ss') : String(righe[i][1]);
      var prova = nome === 'Prova di funzionamento' || /^(PROVA|DEBUG)/.test(disp) ||
        // click di prova fatto da Claude il 17/10/2026 alle 15:19:19 (Forno Sant'Antonio, dal PC)
        (giornoDi(righe[i][0], tz) === '2026-10-17' && ora === '15:19:19' && nome === "Forno Sant'Antonio");
      if (prova) { foglio.deleteRow(i + 2); tolte++; }
    }
  });
  PropertiesService.getScriptProperties().deleteProperty('conti');  // conteggi da ricalcolare
  return tolte;
}
