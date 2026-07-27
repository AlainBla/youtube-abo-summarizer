# Export: schneller erster Paint durch vorgerenderte erste Seite und aufgeteilte Daten

Datum: 2026-07-27
Status: Entwurf, freigegeben zur Planung

## Problem

Ein Export mit ein paar tausend Videos (5–20 MB HTML) zeigt sekundenlang eine
leere Seite. Ursachen, in der Reihenfolge ihres Auftretens:

1. Der gesamte base64-Blob steckt in einem `<script>` am Dateiende und muss vom
   HTML-Parser gelesen werden, bevor irgendein Script läuft.
2. `loadData()` macht `atob()` plus eine byteweise Kopierschleife über den
   kompletten Blob — synchron auf dem Hauptthread.
3. Danach Dekompression und ein `JSON.parse` über *alle* Summaries auf einmal.
4. Erst dann `populateChannelFilter()`, `populateTagFilter()`, Sortierung über
   alle Videos und der Aufbau von 20 Karten.

Vorher ist nichts sichtbar. Zusätzlich startet der Sync-Abruf erst nach Schritt 2
und läuft in zwei seriellen Roundtrips (`/api/whoami`, danach `/api/state`).

## Randbedingungen

- Der Export bleibt **eine portable Datei**. `fetch()` auf Nachbardateien ist
  unter `file://` in allen modernen Browsern blockiert; getrennte Dateien würden
  das lokale Öffnen komplett zerstören. Die Aufteilung passiert deshalb in
  mehrere Blobs *innerhalb* der Datei.
- Kein zusätzlicher Build-Schritt, keine externen Abhängigkeiten im Browser.
- Bestehendes Verhalten (Filter, Sortierung, Pagination, Read/Bookmark, Sync,
  i18n, `--thumbnail`, `--show-model`) bleibt unverändert.

## Architektur

### Dokumentreihenfolge

Der erste Paint hängt daran, dass alles Sichtbare vor den Daten im Dokument
steht:

```
<head>   CSS (inline, wie heute)
<body>   Header, Controls-Bar
         #grid mit den ersten 20 Karten als statisches HTML   <-- Paint passiert hier
         <script> Read/Bookmark-Klassen aus localStorage </script>
         <script> Sync-Requests abfeuern </script>
         <script> UI-Code </script>
         <script> INDEX_B64 + SUM_B64-Chunks </script>          <-- Datenlast ganz zuletzt
```

### 1. Vorgerenderte erste Seite

`renderer.render_export_html()` sortiert die Videos absteigend nach
`published_at` (dieselbe Default-Sortierung wie die UI), rendert die ersten
`PAGE_SIZE` (20) davon per Jinja-Makro als echtes HTML in `#grid` — inklusive
Summary-Vorschau, Tags, Meta-Zeile und Buttons.

Das Makro muss dasselbe Markup erzeugen wie `buildCard()` in JS. Diese
Doppelung ist der bewusst akzeptierte Preis; ein Test hält sie zusammen (siehe
Verifikation).

Nutzen der Übereinstimmung: der erste JS-Render trifft auf identische Karten in
identischer Reihenfolge, `renderPageInPlace()` greift und lässt das DOM stehen —
kein Flackern beim Übergang von statisch zu dynamisch.

### 2. Read/Bookmark-Hydration inline

Direkt nach `#grid` ein kurzes Script, das `yt_read`/`yt_bookmark` aus
`localStorage` liest und `is-read`/`is-bookmarked` sowie die Button-Zustände auf
den statischen Karten setzt. Ohne das blitzen gelesene Karten kurz ungrau auf.

### 3. Sync-Requests früh und parallel

Ein zweites kurzes Script direkt danach:

- harvestet `#session=UUID` aus dem Fragment und schreibt den Token nach
  `localStorage` (heute in `initSync()`),
- feuert bei vorhandenem Token `/api/whoami` **und** `/api/state` sofort ab —
  beide brauchen nur den Token, die heutige Serialisierung ist unnötig,
- legt die Promises in `window.__syncBoot` ab.

`initSync()` konsumiert `window.__syncBoot`, statt selbst zu fetchen. Der
Roundtrip überlappt damit das komplette Dekodieren der Blobs. Die Auswertung
(`applyServerState()`) wartet auf den fertigen Index, wodurch auch die heutige
Race verschwindet, bei der `applyFiltersAndSort()` mit leerem `VIDEOS` einmal
„Keine Videos gefunden" rendert.

### 4. Aufgeteilte Blobs

Statt eines Blobs mit `{index, summaries}`:

| Blob | Inhalt | Größenordnung bei 3000 Videos |
|---|---|---|
| `INDEX_B64` | Array der Metadaten in Default-Sortierreihenfolge (ohne Summary-HTML) | einige hundert KB gzip |
| `SUM_B64[k]` | Map `video_id -> summary_html` für Videos `[k*50, (k+1)*50)` derselben Reihenfolge | je wenige KB |

Chunkgröße: 50 (Konstante im Renderer, kein CLI-Flag — YAGNI).

Ablauf nach dem ersten Paint (`requestAnimationFrame`, damit der Paint zuerst
durchkommt):

1. `INDEX_B64` dekodieren → `VIDEOS`, Dropdowns füllen, Sprache anwenden.
2. Chunk 0 dekodieren (deckt die erste Seite ab).
3. `applyFiltersAndSort()` → in-place-Hydration der statischen Karten.
4. Restliche Chunks per `requestIdleCallback` im Hintergrund nachladen.

`SUMMARIES[id]` wird durch `getSummary(v)` ersetzt, das aus dem Chunk-Cache
liest. `renderPage()` ermittelt vor dem Rendern die benötigten Chunks
(`ensureChunks(slice)`); fehlen welche, bleibt das aktuelle DOM stehen, der
Ergebniszähler zeigt einen Ladehinweis, und der Render läuft, sobald die Chunks
da sind. So kann nie eine Karte mit fälschlich fehlender Summary erscheinen.

### 5. Volltextsuche

`getSearchText()` braucht alle Summaries. Eine Suchanfrage wartet deshalb auf
alle Chunks und zeigt solange „Suche lädt…" im Ergebniszähler; durch den
Idle-Prefetch ist normalerweise schon alles geladen. Teiltreffer auf
unvollständigen Daten werden bewusst nicht ausgeliefert — stillschweigend
fehlende Ergebnisse wären schlimmer als eine kurze Wartezeit.

### 6. `--no-compress`

Der unkomprimierte Pfad behält genau ein Objektliteral `{index, summaries}` wie
heute; nur die vorgerenderte erste Seite kommt hinzu. Chunking gibt es dort
nicht — der Pfad existiert für alte Browser ohne `DecompressionStream`, und die
Datenlast steckt dort ohnehin im HTML-Parse.

### 7. Sprache

Die statischen Karten tragen die Export-Default-Sprache. Weicht die
Browsersprache ab, ersetzt sie der erste JS-Render (`applyLang()` → voller
Rebuild statt in-place). Für Besucher mit abweichender Sprache also ein kurzer
Wechsel — akzeptiert.

## Betroffene Dateien

| Datei | Änderung |
|---|---|
| `renderer.py` | Sortieren, Index/Chunks aufteilen, beide Blob-Varianten rendern, erste Seite vorrendern |
| `export.html.j2` | Dokumentreihenfolge, Jinja-Kartenmakro, Hydration-Script, früher Sync-Boot, `getSummary()`/`ensureChunks()`, Suchverhalten |
| `export.py` | unverändert (keine neuen Flags) |
| `tests/` | Drift-Test Markup, Renderer-Test für Chunk-Aufteilung |
| `README.md`, `CLAUDE.md`, `AGENTS.md` | Beschreibung des neuen Ladeverhaltens |

## Verifikation

1. **Markup-Drift-Test**: dieselben Videodaten einmal durch das Jinja-Makro und
   einmal durch `buildCard()` (Node, wie im bestehenden Harness) rendern,
   normalisiertes HTML vergleichen.
2. **Renderer-Test**: Chunk-Aufteilung deckt jedes Video genau einmal ab,
   Chunk-Zuordnung stimmt mit der Indexreihenfolge überein, erste Seite enthält
   genau die 20 neuesten Videos.
3. **Benchmark**: synthetisches Archiv mit ~3000 Videos erzeugen, alte und neue
   Datei im Browser laden, Zeit bis zur ersten sichtbaren Karte vergleichen.
4. Bestehende Funktionen manuell prüfen: Filter, Sortierung, Pagination, Suche,
   Read/Bookmark, Sprachumschaltung, Sync-Login.

## Bewusst nicht Teil dieser Änderung

- Getrennte Dateien auf der Platte (bricht `file://`).
- Ein serverseitiger Such-Index.
- Virtualisiertes Scrollen / Ersatz der Pagination.
- Konfigurierbare Chunk- oder Seitengröße.
