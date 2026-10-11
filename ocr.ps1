# ocr.ps1 - legge il testo scritto dentro un'immagine con l'OCR integrato di Windows 10/11.
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File ocr.ps1 "C:\percorso\immagine.jpg"
# Stampa il testo riconosciuto (UTF-8), una riga per riga della foto; dove nella foto c'è uno
# spazio verticale più grande del normale tra due righe (interlinea maggiorata tra gruppi di
# pietanze) stampa una riga vuota. Usato da Menu.py per leggere gli avvisi nelle foto
# (es. "Siamo chiusi da domenica 4/10 a mercoledì 7/10"). Non serve installare nulla.
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

# le funzioni di Windows sono "asincrone": questo aiutante aspetta il risultato
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $task = $asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($WinRtTask))
    $task.Wait(-1) | Out-Null
    $task.Result
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $Path).Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('it'))
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) { throw 'OCR di Windows non disponibile (manca la lingua per il riconoscimento del testo)' }

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = @($result.Lines)
try {
    # altezza tipica di una riga (mediana): uno spazio tra due righe più alto di 0,9 righe = rigo vuoto
    $alte = @($lines | ForEach-Object { ($_.Words | ForEach-Object { $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum } | Sort-Object)
    $tipica = if ($alte.Count) { $alte[[int][math]::Floor($alte.Count / 2)] } else { 0 }
    $sottoPrima = $null
    foreach ($l in $lines) {
        $sopra = ($l.Words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
        $sotto = ($l.Words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
        if ($null -ne $sottoPrima -and $tipica -gt 0 -and ($sopra - $sottoPrima) -gt 0.9 * $tipica) { '' }
        $l.Text
        $sottoPrima = $sotto
    }
} catch {
    $lines | ForEach-Object { $_.Text }   # se qualcosa va storto: solo le righe, come prima
}
