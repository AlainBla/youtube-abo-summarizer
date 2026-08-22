# Design: EPUB-Export ("Ebook") aus dem Video-Store

Stand: 2026-08-22. Ziel ist ein `ebook.py`-CLI, das gespeicherte Video-Zusammenfassungen
als EPUB-3-Datei ausgibt — lesbar auf Kindle (Send-to-Kindle), Tolino, Kobo, Apple Books.

## Ziel und Nicht-Ziele

**Ziel:** Ein Buch aus dem bestehenden Store bauen, ohne YouTube- oder LLM-Aufrufe.
Auswahl wie beim HTML-Export, Gliederung nach Kalenderwoche, optional getrennt nach
ungelesen/gelesen anhand des Sync-Server-Zustands eines Nutzers.

**Nicht-Ziele (bewusst später):** PDF, Versand per Mail/Send-to-Kindle, interaktive Auswahl
im HTML-Archiv, gestaltetes Cover-Bild, Mehrfachbücher in einem Lauf.

## Entscheidungen

| Frage | Entscheidung | Begründung |
|---|---|---|
| Format | EPUB 3 (+ `toc.ncx` für alte Reader) | EPUB ist ZIP + XHTML: baubar mit `zipfile` und Jinja2, **keine neue Abhängigkeit** |
| Gliederung | Kapitel je **Kalenderwoche** (ISO), Videos darin chronologisch | vom Nutzer gewählt; hält das Inhaltsverzeichnis auch bei 100 Einträgen kurz |
| Gelesen/Ungelesen | Zwei Teile: "Ungelesen" vor "Gelesen", je mit Wochenkapiteln | `--read split` (Default), `--read drop` wirft Gelesenes raus, `--read ignore` ignoriert den Status |
| Nutzerbezug | Lesestatus aus `sync-server/sync.db` (lokal, lesend) | Der Sync-Server läuft auf derselben Maschine; kein Token-Handling, kein neuer Endpunkt |
| Inhalt je Video | Metadaten + Zusammenfassung + eingebettetes Thumbnail + Transkript im Anhang | vom Nutzer gewählt |
| Umfang | `--limit 100` (neueste), `--limit 0` = alle | Store hat ~4.900 Videos; ein unbegrenztes Buch ist unbrauchbar groß |
| Auswahl | dieselben Flags wie `export.py` plus `--tag` | vertraute Bedienung |

### Verworfen: Sync-Zustand über die HTTP-API

`GET /api/state` mit Session-Token wäre remote-fähig, verlangt aber ein 30-Tage-Token aus dem
Browser-`localStorage` im CLI-Aufruf. Der lesende Zugriff auf `sync.db` ist für den Betrieb auf
einem Host einfacher und robuster. Bleibt die Datei unerreichbar (anderer Host), ist der
API-Weg eine spätere Ergänzung — die Trennung dafür liegt in `_load_user_state()`.

## CLI

```
python ebook.py [Auswahl] [--limit N] [--user EMAIL [--sync-db PATH] [--read MODE]]
                [--no-thumbnails] [--no-transcripts] [--output FILE] [--lang de|en]
```

| Flag | Default | Wirkung |
|---|---|---|
| `--hours N` / `--all` / `--channel ID` / `--videos a,b,c` | neueste 100 | Auswahl wie `export.py`; `--hours` und `--all` schließen sich aus |
| `--tag TAG` | — | zusätzlicher Filter auf die gespeicherten Tags |
| `--limit N` | `100` | die N neuesten der Auswahl; `0` = unbegrenzt |
| `--user EMAIL` | — | lädt Lesestatus aus der Sync-DB; ohne das Flag gibt es nur einen Teil |
| `--sync-db PATH` | `sync-server/sync.db` | Pfad zur Sync-Datenbank |
| `--read split\|drop\|ignore` | `split` | Gelesenes nach hinten / weglassen / Status ignorieren |
| `--no-thumbnails` | aus | Thumbnails werden sonst geladen und eingebettet |
| `--no-transcripts` | aus | Transkript-Anhang wird sonst erzeugt |
| `--output FILE` | `ebook_YYYY-MM-DD_HH-MM.epub` | Zieldatei |
| `--lang de\|en` | `de` | Sprache der Buchtexte (Titel, Teil- und Kapitelnamen) |

Verhalten ohne Treffer: Meldung und Exit 0 — wie `export.py`.

## Buchaufbau

```
Cover (XHTML)
Titelseite: Zeitraum, Anzahl Videos, Erzeugungsdatum
Inhalt (nav.xhtml)
├── Teil "Ungelesen"            (nur bei --user und --read split)
│   ├── KW 33 · 11.–17. August 2026
│   │   ├── Videotitel  (Kanal · Datum · Dauer · Tags)
│   │   │   Thumbnail, Zusammenfassung, Link zu YouTube, Link zum Transkript
│   │   └── …
│   └── KW 34 · 18.–24. August 2026
├── Teil "Gelesen"
│   └── …
└── Anhang "Transkripte"
    └── je Video eine Seite mit Rücksprung ins Kapitel
```

- Eine XHTML-Datei je Wochenkapitel, Videos als `<section id="v-VIDEOID">` darin.
- Transkripte je eine eigene XHTML-Datei (`transcript-VIDEOID.xhtml`), beidseitig verlinkt;
  ohne sie bleibt das Buch klein, deshalb `--no-transcripts` als Ausweg.
- Bilder unter `OEBPS/images/VIDEOID.jpg`, heruntergeladen und lokal in `data/thumbnails/`
  zwischengespeichert, damit ein zweiter Lauf ohne Netz auskommt.
- Timestamp-Links der Zusammenfassung bleiben als externe YouTube-Links erhalten.

## Datenfluss

1. Auswahl aus `store` (`get_all_videos()` / `get_videos_since()`), danach Filter für
   `--channel`, `--videos`, `--tag`.
2. Sortierung `published_at` absteigend, `--limit` anwenden — die Begrenzung greift **vor**
   der Gruppierung, damit "die 100 neuesten" auch wirklich das bedeutet.
3. `_load_user_state(sync_db, email)` → Menge gelesener Video-IDs (leer ohne `--user`).
4. Partition in ungelesen/gelesen (`--read`), je Teil Gruppierung nach ISO-Woche
   (`date.isocalendar()`), Kapitel chronologisch aufsteigend, Videos darin ebenso.
5. Rendern der XHTML-Dateien über Jinja2-Templates (`ebook/*.xhtml.j2`).
6. Packen: `mimetype` als erster Eintrag **unkomprimiert**, danach `META-INF/container.xml`,
   `OEBPS/content.opf`, `nav.xhtml`, `toc.ncx`, CSS, Kapitel, Transkripte, Bilder.

## Heikle Stellen

**XHTML muss wohlgeformtes XML sein.** Die gespeicherten Zusammenfassungen sind
HTML-Fragmente (nh3-Allowlist: `h3 p ul ol li a strong em`). Vor dem Einbau läuft jedes
Fragment durch `_xhtmlify()`: benannte Entities ersetzen (`&nbsp;` → `&#160;`), dann
Probe-Parse mit `xml.etree.ElementTree`. Scheitert das, wird das Fragment als escapeter
Text eingebettet statt das ganze Buch unlesbar zu machen. Reader sind hier gnadenlos —
ein einziges nicht geschlossenes Tag kann das Buch abweisen.

**Thumbnails brauchen Netz.** Fehlschläge (Timeout, 404) überspringen nur das Bild und
werden am Ende gezählt gemeldet; der Buchbau bricht nie daran ab. Nur `https`-URLs werden
geladen, Größe pro Bild begrenzt.

**Sync-DB.** Rein lesender Zugriff, unbekannte E-Mail ist ein klarer Fehler statt eines
stillen leeren Lesestatus. Fehlt die Datei und wurde `--user` gesetzt: Abbruch mit Hinweis.

**Wochengrenzen.** ISO-Wochen laufen über Jahresgrenzen (KW 1 kann im Dezember beginnen);
Gruppierungsschlüssel ist `(iso_year, iso_week)`, nicht die Wochennummer allein.

## Tests

| Bereich | Prüfung |
|---|---|
| Auswahl | `--limit` greift vor der Gruppierung; `--tag`/`--channel` filtern korrekt |
| Wochen | ISO-Gruppierung inkl. Jahreswechsel; Kapitelreihenfolge chronologisch |
| Lesestatus | `split` sortiert Gelesenes nach hinten, `drop` entfernt es, `ignore` ignoriert es; unbekannte E-Mail bricht ab |
| EPUB-Struktur | `mimetype` ist erster Eintrag und unkomprimiert; jede Datei im ZIP steht im OPF-Manifest und umgekehrt; Spine-Reihenfolge entspricht dem Buchaufbau |
| XHTML | jede erzeugte Datei parst als XML; kaputte Fragmente landen escapet statt roh |
| Bilder | Fehlschlag beim Laden überspringt nur das Bild (gemockter Fetcher, kein Netz im Test) |
| Transkripte | Anhang-Seiten existieren, Hin- und Rücklink zeigen auf vorhandene Anker |

## Dateien

```
ebook.py                    # CLI: Auswahl, Lesestatus, Aufruf des Builders
epub_builder.py             # Gruppierung, XHTML-Rendering, ZIP-Packung
ebook/book.css              # Typografie (hell/neutral, Reader steuert die Farben)
ebook/*.xhtml.j2            # cover, title, chapter, transcript, nav, opf, ncx
tests/test_ebook_selection.py
tests/test_epub_structure.py
```

`renderer.py` und `export.py` bleiben unangetastet. `store.py` bekommt einen
abwärtskompatiblen Schalter `with_transcripts=False`, damit die Auswahl nicht für ~4.900
Videos die Transkriptdateien von der Platte liest; `i18n.py` bekommt die Buchtexte
(Teil-, Kapitel- und Anhangsbezeichnungen).
