# Kontrolliertes deutsches Tag-Vokabular

Stand: 2026-09-03

## Problem

Der Summarize-Prompt lässt das Modell 3–7 englische Tags frei erfinden. Das
Ergebnis im Store:

| | |
|---|---|
| Videos | 5486 (5209 mit Tags) |
| Tag-Instanzen | 29 226, Ø 5,33 pro Video |
| eindeutige Tags | **10 700** |
| davon genau einmal vergeben | 7542 (70 %) |
| Anteil der Top 100 an allen Instanzen | 25 % |

Ein Tag, der einmal vorkommt, filtert nichts. Er kostet aber:

* `populateTagFilter()` in `export.html.j2` baut für jeden vorkommenden Tag eine
  `<option>` — bei `export.py --all` also rund 10 700 Einträge im Dropdown.
* Die Tags belegen im eingebetteten Index 480 KB roh, **129 KB gzip**. Das Archiv
  wird ausgeliefert; der Anteil wandert bei jedem Abruf über die Leitung.

Normalisierung löst das nicht: Groß/Kleinschreibung, Plural und Apostroph
zusammengeführt ergeben 10 446 statt 10 700 Tags — 254 Zusammenlegungen. Das
Muster ist nicht Schreibweise, sondern Erfindung: `Strait of Hormuz`,
`Pony Island 2`, `Elon Ruskin`. Ein Prompt, der sich Tags wünscht, bekommt neue
Tags. Das Vokabular muss vorgegeben und durchgesetzt werden.

Zweiter Bruch: Der Prompt verlangt Englisch, die Oberfläche ist deutsch.

## Entscheidungen

1. **Feste Liste, rund 160 Tags, ausschließlich Deutsch.** Kein zweistufiges
   Modell mit freiem Feinthema — eine Tag-Art, ein Datenfeld.
2. **Streng.** Was nicht auf der Liste steht, kommt nicht in den Store.
3. **Migration per LLM-Zuordnung des Vokabulars**, nicht per Neuzusammenfassung:
   die 10 700 Alt-Begriffe werden einmalig auf die Liste abgebildet, nicht die
   5209 Videos einzeln neu verarbeitet.
4. **Eigennamen gehören nicht ins Vokabular** — keine Spieltitel, Firmen,
   Personen, Einzelorte. Genau die haben die 7542 Einmal-Tags erzeugt. Sie
   bleiben über die Volltextsuche auffindbar, die Titel und Zusammenfassung
   durchsucht.

## Vokabular

`tags.py` hält das Vokabular als `VOCABULARY: dict[str, tuple[str, ...]]`
(Gruppe → Tags) und daraus abgeleitet `ALL_TAGS: frozenset[str]`. 161 Tags in
zehn Gruppen: Spiele-Genres (16), Gaming (19), Spiele-Plattformen (10), Technik
und KI (23), Wissenschaft (7), Politik und Gesellschaft (24), Krieg und
Sicherheit (17), Länder und Regionen (10), Wirtschaft (13), Medien und Kultur
(22).

Die Gruppen dienen der Lesbarkeit und dem Prompt. Beim Filtern ist ein Tag ein
Tag; die Gruppe wird nicht gespeichert und nicht ausgeliefert.

Deckungsprobe gegen den bestehenden Store (grobe Schlüsselwortregel, also eine
Untergrenze): 5055 der 5209 getaggten Videos (97 %) behalten mindestens einen
Tag, 145 der 161 Tags werden benutzt.

## Bausteine

### `tags.py` — Vokabular, Torwächter, Werkzeug

```python
VOCABULARY: dict[str, tuple[str, ...]]
ALL_TAGS: frozenset[str]

def prompt_block() -> str
def canonicalize(raw: list[str]) -> tuple[list[str], list[str]]
def load_aliases() -> dict[str, list[str]]
def record_candidates(rejected: list[str]) -> None
```

`canonicalize()` ist der Torwächter und die einzige Stelle, die entscheidet, was
ein Tag ist. Reihenfolge pro Rohtag:

1. exakter Treffer in `ALL_TAGS` → übernehmen
2. Treffer ohne Rücksicht auf Groß/Kleinschreibung → auf die kanonische
   Schreibweise abbilden
3. Treffer in der Alias-Tabelle → auf deren Zielwerte abbilden (0–2 Tags)
4. sonst → verwerfen und als Kandidat zurückgeben

Rückgabe ist `(behaltene Tags, verworfene Rohtags)`. Behaltene Tags werden
dedupliziert, behalten die Reihenfolge ihres ersten Auftretens und werden bei
fünf abgeschnitten. Ein Video ohne passenden Tag bekommt eine leere Liste; das
ist erlaubt und besser als ein erfundener Tag.

`prompt_block()` rendert die Liste gruppiert für den Prompt. Umfang etwa 2 KB,
also grob 700 Tokens pro Zusammenfassung zusätzlich.

CLI:

| Aufruf | Wirkung |
|---|---|
| `python tags.py --list` | Vokabular nach Gruppen ausgeben |
| `python tags.py --candidates [--min N]` | abgelehnte Vorschläge nach Häufigkeit |
| `python tags.py --build-aliases [--limit N] [--dry-run]` | Alias-Tabelle per LLM erweitern |

### `tag_aliases.json` — Alias-Tabelle, versioniert

Flache Abbildung `alter Tag → Liste kanonischer Tags` im Repository-Wurzel-
verzeichnis, nach Schlüsseln sortiert. Eine leere Liste heißt „bewusst nicht
zugeordnet" und wird bei einem erneuten `--build-aliases` nicht neu angefragt.
Bei 10 700 Einträgen rund 400 KB — einmalige Migrationsfracht, die danach als
Fangnetz für Modellausrutscher in Betrieb bleibt.

`--build-aliases` schickt die noch nicht zugeordneten Begriffe in Stapeln von
100 an das Modell, zusammen mit dem Vokabular, und erwartet JSON. Jeder Zielwert
wird gegen `ALL_TAGS` geprüft; was nicht darin steht, wird verworfen, nicht
gespeichert. Nach jedem Stapel wird geschrieben, damit ein Abbruch nichts
kostet und der nächste Lauf dort weitermacht. Bei 10 700 Begriffen etwa 107
Aufrufe ohne Transkripte.

### `data/tag_candidates.json` — Kandidatenprotokoll

`{"Rohtag": {"count": 12, "last_seen": "2026-09-03T10:00:00Z"}}`, liegt im
gitignorierten `data/`, weil es Betriebsdaten sind. Geschrieben von
`record_candidates()`, gelesen von `tags.py --candidates`.

Das ist der Wachstumsprozess: Was das Modell wiederholt vorschlägt und was
fehlt, steht dort mit Zahl daneben. Aufnahme in die Liste ist ein Commit in
`tags.py` — bewusst, nachvollziehbar, umkehrbar. Ohne diesen Schritt wächst das
Vokabular nicht.

### `openrouter.py` — Erzeugung

* Prompt: statt „3–7 englische Tags" jetzt 3–5 Tags **aus der übergebenen
  Liste**, deutsch, kein Erfinden; passt nichts, lieber weniger.
* `summarize_video()` schickt das Ergebnis von `_parse_tags()` durch
  `tags.canonicalize()` und die Ablehnungen durch `tags.record_candidates()`.
  Zurück kommt wie bisher `(summary_html, tags_list)` — nur eben gefiltert.

Der Prompt ist die Bitte, `canonicalize()` der Zaun. Beides zusammen, weil ein
Modell, dem man die Liste zeigt, seltener daneben liegt, aber nie nie.

### `repair.py --remap-tags` — Migration des Stores

Läuft über alle Store-Einträge, schickt die gespeicherten Tags durch
`canonicalize()` und schreibt das Ergebnis zurück. Berührt Zusammenfassungen und
Transkripte nicht.

Dafür braucht `store.py` eine eigene kleine Funktion:

```python
def update_tags(video_id: str, tags: list[str]) -> None
```

`update_video_with_summary(tags=...)` ist hier nicht benutzbar: sie setzt
`transcript_error` und `summary_model` bedingungslos mit, ein Aufruf nur für
Tags würde beide Spalten auf `NULL` zurücksetzen.

`--dry-run` schreibt nichts und berichtet: wie viele Videos sich ändern, wie
viele danach ohne Tag dastehen, welche Tag-Verteilung entsteht. Erwartung: Ø
5,33 Tags pro Video fällt auf 2–3, weil viele Alt-Tags auf denselben Zielwert
fallen.

Vor dem Schreiben ist `data/videos.db` zu sichern — `data/` ist gitignoriert,
der Schreibvorgang ist an Ort und Stelle.

## Was sich nicht ändert

* `export.py`, `export.html.j2`: kein Eingriff nötig. Das Dropdown baut sich aus
  den vorkommenden Tags, das sind dann ≤161 statt 10 700. Der Tag-Anteil im Index
  fällt von 129 KB gzip auf wenige KB. Eine Indexkodierung der Tags (Tag →
  Nummer) wäre danach marginal und bleibt draußen.
* `store.py`: Schema unverändert, `tags TEXT` bleibt ein JSON-Array.
* `sync-server/`: unberührt, Tags werden dort nicht gespeichert.
* `ebook.py --tag`: unberührt, prüft gegen die gespeicherten Tags.
* Zusammenfassungen werden nicht neu erzeugt.

Bewusst hingenommen: Bei `--lang en` zeigt die Oberfläche englische Bedienung
mit deutschen Tags. Eine Übersetzungstabelle wäre ein zweites Vokabular mit
zweiter Pflege; dafür ist der Nutzen zu klein.

## Tests

* `tests/test_tags_vocabulary.py` — keine Dubletten über Gruppen hinweg, jeder
  Tag nichtleer und ohne führende/folgende Leerzeichen, `ALL_TAGS` deckt sich
  mit `VOCABULARY`, `prompt_block()` enthält jeden Tag.
* `tests/test_tags_canonicalize.py` — exakter Treffer; Treffer nur über
  Groß/Kleinschreibung; Alias auf einen und auf zwei Zielwerte; unbekannter Tag
  wird verworfen und zurückgemeldet; Duplikate fallen weg; mehr als fünf werden
  abgeschnitten; leere Eingabe ergibt leere Ausgabe.
* `tests/test_tags_aliases.py` — Zielwerte außerhalb des Vokabulars werden beim
  Einlesen verworfen; leere Liste bedeutet „nicht zuordnen" und wird nicht als
  fehlend behandelt.
* `tests/test_repair_remap_tags.py` — `--dry-run` schreibt nichts; Remap
  dedupliziert; ein Video ohne zuordenbaren Tag endet mit leerer Liste, nicht
  mit `null`; `store.update_tags()` lässt `transcript_error` und `summary_model`
  in Ruhe.
* `tests/test_openrouter_prompt.py` (bestehend) — erweitert: der Prompt enthält
  die Liste und verlangt Deutsch.

## Reihenfolge der Umsetzung

1. `tags.py` mit Vokabular, `canonicalize()`, `prompt_block()` plus Tests, dazu
   `store.update_tags()`. Ohne Verdrahtung, ohne Wirkung.
2. Prompt und Torwächter in `openrouter.py`. Ab hier sind **neue** Videos
   richtig getaggt; der Altbestand ist unberührt.
3. `tags.py --build-aliases` laufen lassen, eine Stichprobe der Zuordnung
   durchsehen, `tag_aliases.json` committen.
4. `repair.py --remap-tags --dry-run` prüfen, `data/videos.db` sichern, dann
   anwenden.
5. Archiv neu exportieren, auf dem Host dasselbe.
6. `CLAUDE.md`, `README.md` und `AGENTS.md` nachziehen.

Schritt 2 ist die Blutung gestoppt, Schritt 4 die Aufräumarbeit. Zwischen beiden
darf beliebig Zeit liegen.

## Risiken

* **Das Modell hält sich nicht an die Liste.** Aufgefangen durch
  `canonicalize()`; sichtbar über `--candidates`. Häufen sich dort sinnvolle
  Vorschläge, fehlt der Liste etwas.
* **Die Alias-Zuordnung liegt falsch.** Vor dem Anwenden Stichprobe, Tabelle
  versioniert, Store gesichert, Vorgang wiederholbar.
* **Videos verlieren Tags.** Bei 97 % Deckung bleibt ein Rest ohne Tag. Das ist
  ehrlicher als ein Tag, der nichts filtert.
* **Prompt wird länger.** Etwa 700 Tokens pro Zusammenfassung — bei
  Transkripten von mehreren Zehntausend Tokens nicht messbar.
