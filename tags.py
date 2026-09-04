"""Controlled German tag vocabulary.

The LLM used to invent tags freely, which produced 10 700 distinct English tags
across 5486 videos — 7542 of them used exactly once. A tag that appears once
filters nothing; it only makes the archive's tag menu unusable and its payload
larger. So the vocabulary is fixed here, in the repository, and grows only by a
commit.

The list is derived from the archive's own tag history: the recurring themes of
the subscribed channels, named in German because the interface is German.
Groups exist for readability and for the prompt; they carry no meaning at
filter time, where a tag is just a tag.
"""

import argparse
import collections
import functools
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VOCABULARY: dict[str, tuple[str, ...]] = {
    "Spiele-Genres": (
        "Rollenspiel",
        "Action-Adventure",
        "Shooter",
        "Strategiespiel",
        "Simulation",
        "Jump-and-Run",
        "Metroidvania",
        "Roguelike",
        "Soulslike",
        "Horror-Spiel",
        "Survival",
        "Rennspiel",
        "Kampfspiel",
        "Puzzlespiel",
        "Multiplayer",
        "Open World",
    ),
    "Gaming": (
        "Gaming",
        "Indie-Spiele",
        "Retro-Gaming",
        "Spieletest",
        "Spielvorschau",
        "Spielentwicklung",
        "Spieldesign",
        "Leveldesign",
        "Narratives Design",
        "Spielebranche",
        "Gaming-News",
        "Gaming-Kultur",
        "Let's Play",
        "Speedrun",
        "Emulation",
        "Spielekonservierung",
        "Modding",
        "Remake und Remaster",
        "Pixel-Art",
    ),
    "Spiele-Plattformen": (
        "PC-Gaming",
        "Konsolen",
        "Handheld",
        "VR-Gaming",
        "Mobile Gaming",
        "Nintendo",
        "PlayStation",
        "Xbox",
        "Steam",
        "Linux-Gaming",
    ),
    "Technik und KI": (
        "Künstliche Intelligenz",
        "Sprachmodelle",
        "KI-Agenten",
        "Lokale KI",
        "Prompt Engineering",
        "KI-Ethik",
        "KI-Regulierung",
        "Softwareentwicklung",
        "Programmierung",
        "Open Source",
        "Linux",
        "Betriebssysteme",
        "Cybersicherheit",
        "Datenschutz",
        "Überwachung",
        "Hardware",
        "Chips und Halbleiter",
        "Robotik",
        "Automatisierung",
        "Tech-News",
        "Big Tech",
        "Soziale Medien",
        "Kryptowährung",
    ),
    "Wissenschaft": (
        "Wissenschaft",
        "Raumfahrt",
        "Astronomie",
        "Medizin und Gesundheit",
        "Psychologie",
        "Mentale Gesundheit",
        "Umwelt und Natur",
    ),
    "Politik und Gesellschaft": (
        "Politik",
        "US-Politik",
        "Deutsche Politik",
        "Europäische Politik",
        "Wahlen",
        "Demokratie",
        "Rechtsextremismus",
        "Populismus",
        "Autoritarismus",
        "Korruption",
        "Menschenrechte",
        "Meinungsfreiheit",
        "Pressefreiheit",
        "Justiz und Recht",
        "Verbraucherschutz",
        "Arbeitswelt",
        "Soziale Ungleichheit",
        "Migration",
        "Bildung",
        "Öffentliche Gesundheit",
        "Klimawandel",
        "Energiepolitik",
        "Nachhaltigkeit",
        "Stadtentwicklung",
    ),
    "Krieg und Sicherheit": (
        "Geopolitik",
        "Internationale Beziehungen",
        "NATO",
        "Diplomatie",
        "Außenpolitik",
        "Ukraine-Krieg",
        "Nahost-Konflikt",
        "Militärstrategie",
        "Militärtechnik",
        "Drohnenkrieg",
        "Seekrieg",
        "Rüstungsindustrie",
        "Sanktionen",
        "Kriegsverbrechen",
        "Propaganda",
        "Schifffahrt und Logistik",
        "Nationale Sicherheit",
    ),
    "Länder und Regionen": (
        "Deutschland",
        "USA",
        "Russland",
        "Ukraine",
        "Iran",
        "Israel",
        "China",
        "Japan",
        "Europäische Union",
        "Naher Osten",
    ),
    "Wirtschaft": (
        "Wirtschaft",
        "Börse und Aktien",
        "Geldanlage",
        "Inflation",
        "Lieferketten",
        "Welthandel",
        "Arbeitsmarkt",
        "Unternehmensstrategie",
        "Produktmanagement",
        "Produktivität",
        "Marketing und Werbung",
        "Start-ups",
        "Energiemarkt",
    ),
    "Medien und Kultur": (
        "Satire",
        "Comedy",
        "Late-Night",
        "Parodie",
        "Medienkritik",
        "Journalismus",
        "Podcast",
        "Interview",
        "Dokumentation",
        "Livestream",
        "Reaktionsvideo",
        "Musik",
        "Musikproduktion",
        "Film und Serien",
        "Animation",
        "Kunst und Design",
        "Popkultur",
        "Geschichte",
        "Sprache und Etymologie",
        "Wissensvermittlung",
        "Gesellschaftskritik",
        "Nostalgie",
    ),
}

ALL_TAGS: frozenset[str] = frozenset(t for group in VOCABULARY.values() for t in group)


MAX_TAGS = 5

ALIASES_PATH = Path(__file__).parent / "tag_aliases.json"

# Lookup for tags that differ from a vocabulary entry only in case.
_LOWER_INDEX: dict[str, str] = {t.lower(): t for t in ALL_TAGS}


@functools.lru_cache(maxsize=1)
def load_aliases() -> dict[str, list[str]]:
    """Read the alias table: old tag (lowercased) → canonical tags.

    Targets that are not in the vocabulary are dropped here rather than
    trusted, so a stale or hand-edited table can never smuggle a tag in. A
    missing file is normal — before the migration there is none.
    """
    if not ALIASES_PATH.exists():
        return {}
    try:
        raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    clean: dict[str, list[str]] = {}
    for old, targets in raw.items():
        if isinstance(targets, list):
            clean[old.lower()] = [t for t in targets if t in ALL_TAGS]
    return clean


def canonicalize(raw: list[str]) -> tuple[list[str], list[str]]:
    """Reduce raw tag suggestions to the controlled vocabulary.

    Returns (kept, rejected). Kept tags are deduplicated, keep the order of
    their first appearance and are cut at MAX_TAGS. Rejected raw tags are
    handed back for the candidate log — an alias that deliberately maps to
    nothing is not among them.
    """
    aliases = load_aliases()
    kept: list[str] = []
    rejected: list[str] = []
    for tag in raw:
        cleaned = tag.strip()
        if not cleaned:
            continue
        lower = cleaned.lower()
        if cleaned in ALL_TAGS:
            targets = [cleaned]
        elif lower in _LOWER_INDEX:
            targets = [_LOWER_INDEX[lower]]
        elif lower in aliases:
            targets = aliases[lower]
        else:
            rejected.append(cleaned)
            continue
        for target in targets:
            if target not in kept:
                kept.append(target)
    return kept[:MAX_TAGS], rejected


CANDIDATES_PATH = Path(__file__).parent / "data" / "tag_candidates.json"


def load_candidates() -> dict[str, dict]:
    """Read the candidate log; an absent or damaged file reads as empty."""
    if not CANDIDATES_PATH.exists():
        return {}
    try:
        data = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_candidates(rejected: list[str]) -> None:
    """Count rejected tag suggestions so the vocabulary's gaps become visible.

    Best effort on purpose: a summarize run must not fail because a log file
    cannot be written. Written through a temp file so a killed process cannot
    leave a truncated log behind.
    """
    if not rejected:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = load_candidates()
    for tag in rejected:
        entry = data.setdefault(tag, {"count": 0, "last_seen": now})
        entry["count"] += 1
        entry["last_seen"] = now
    try:
        CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CANDIDATES_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(CANDIDATES_PATH)
    except OSError:
        pass


def prompt_block() -> str:
    """Render the vocabulary for the system prompt: one line per group.

    Grouping costs a few tokens and buys the model a map of the space, which
    makes it pick a neighbouring tag instead of inventing one.
    """
    return "\n".join(
        f"{group}: {', '.join(entries)}" for group, entries in VOCABULARY.items()
    )


ALIAS_BATCH = 100

# At most two vocabulary entries per old tag: three or more turn every video
# into a wall of chips and defeat the point of a small vocabulary.
_MAX_ALIAS_TARGETS = 2


def parse_alias_response(text: str, batch: list[str]) -> dict[str, list[str]]:
    """Read one batch's mapping out of the model's answer.

    Everything unverifiable is dropped rather than trusted: targets outside the
    vocabulary, keys that were not asked for, values that are not lists. A term
    the model skipped is simply absent from the result, which leaves it in the
    queue for a later run instead of silently marking it unmappable.
    """
    cleaned = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned).strip()
    try:
        raw = json.loads(cleaned)
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    asked = set(batch)
    mapping: dict[str, list[str]] = {}
    for old, targets in raw.items():
        if old not in asked or not isinstance(targets, list):
            continue
        seen: list[str] = []
        for target in targets:
            if isinstance(target, str) and target in ALL_TAGS and target not in seen:
                seen.append(target)
        mapping[old] = seen[:_MAX_ALIAS_TARGETS]
    return mapping


_ALIAS_SYSTEM_PROMPT = """Du ordnest alte, frei erfundene Video-Tags einem festen deutschen Tag-Vokabular zu.

Für jeden vorgelegten Tag nennst du die Vokabular-Einträge, die dasselbe Thema bezeichnen —
höchstens zwei, und ausschließlich Einträge, die unten wörtlich vorkommen.
Ein Eigenname (Spieltitel, Firma, Person, einzelner Ort) wird dem allgemeinen Thema
zugeordnet, zu dem er gehört: ein Spieltitel seinem Genre, eine Firma ihrem Feld, ein
Ort der Region oder dem Konflikt.

Die leere Liste ist das letzte Mittel, nicht die sichere Standardantwort. Geh das
ganze Vokabular durch, bevor du dich für eine leere Liste entscheidest — für die
meisten Tags gibt es einen thematisch passenden Eintrag, auch wenn kein Wort
übereinstimmt. Nur ein Tag ganz ohne inhaltlichen Bezug zu irgendeinem Eintrag
bleibt leer.

Beispiele:
- Firma zu ihrem Feld: "Valve" -> ["Spielebranche"]
- Spieltitel zu seinem Genre: "Bloodborne" -> ["Soulslike"]
- Veranstaltung zur News-Kategorie: "Gamescom" -> ["Gaming-News"]
- Rollenbezeichnung zu ihrer Disziplin: "AI Product Management" -> ["Produktmanagement"]
- echter Nicht-Treffer (Kanalname ohne Themenbezug): "Rocket Beans" -> []

Antworte mit genau einem JSON-Objekt: {{"alter Tag": ["Vokabular-Tag", ...], ...}}
Kein Prosatext, kein Code-Fence, keine Erklärung.

Vokabular:
{vocabulary}"""


def stored_tags() -> list[str]:
    """Every distinct tag currently in the store, most frequent first.

    Frequent terms first means a run cut short by --limit has mapped the tags
    that matter for the most videos.
    """
    import store

    counter: collections.Counter[str] = collections.Counter()
    for entry in store.get_all_videos(with_transcripts=False):
        counter.update(entry.get("tags") or [])
    return [tag for tag, _count in counter.most_common()]


def _read_aliases_raw() -> dict[str, list[str]]:
    """The alias file as written, original spelling of the keys preserved."""
    if not ALIASES_PATH.exists():
        return {}
    try:
        data = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_aliases(mapping: dict[str, list[str]]) -> None:
    tmp = ALIASES_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(ALIASES_PATH)
    load_aliases.cache_clear()


def build_aliases(limit: int | None, dry_run: bool, model: str) -> None:
    """Extend tag_aliases.json by mapping unmapped store tags onto the vocabulary.

    Maps the vocabulary, not the videos: one pass over the distinct old tags
    instead of 5209 summarize runs. Writes after every batch, so an abort costs
    nothing and the next run continues where this one stopped.
    """
    import openrouter          # local import: openrouter imports this module

    known = {key.lower() for key in _read_aliases_raw()}
    todo: list[str] = []
    queued: set[str] = set()
    for t in stored_tags():
        lower = t.lower()
        if t in ALL_TAGS or lower in known or lower in queued:
            continue
        todo.append(t)
        queued.add(lower)
    if limit:
        todo = todo[:limit]
    batches = [todo[i : i + ALIAS_BATCH] for i in range(0, len(todo), ALIAS_BATCH)]
    print(f"{len(todo)} unzugeordnete Tags in {len(batches)} Stapel(n) à {ALIAS_BATCH}")
    if dry_run:
        print("(dry-run — keine Modellaufrufe, keine Änderungen)")
        return
    if not todo:
        return

    client = openrouter.build_client()
    system = _ALIAS_SYSTEM_PROMPT.format(vocabulary=prompt_block())
    mapping = _read_aliases_raw()
    for number, batch in enumerate(batches, start=1):
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(batch)},
            ],
            max_tokens=4096,
        )
        content = response.choices[0].message.content or ""
        result = parse_alias_response(content, batch)
        mapping.update(result)
        _write_aliases(mapping)
        print(f"  Stapel {number}/{len(batches)}: {len(result)} von {len(batch)} zugeordnet")
    print(f"\nFertig. {len(mapping)} Einträge in {ALIASES_PATH.name}.")


def _print_vocabulary() -> None:
    for group, entries in VOCABULARY.items():
        print(f"\n{group} ({len(entries)})")
        print("  " + " · ".join(entries))
    print(f"\n{len(ALL_TAGS)} Tags in {len(VOCABULARY)} Gruppen")


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled German tag vocabulary.")
    parser.add_argument("--list", action="store_true", help="Print the vocabulary by group.")
    parser.add_argument(
        "--candidates",
        action="store_true",
        help="Print rejected tag suggestions by frequency (from data/tag_candidates.json).",
    )
    parser.add_argument(
        "--min",
        type=int,
        default=1,
        metavar="N",
        help="With --candidates: only show suggestions seen at least N times (default 1).",
    )
    parser.add_argument(
        "--build-aliases",
        action="store_true",
        help="Map unmapped store tags onto the vocabulary via the LLM and write tag_aliases.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="With --build-aliases: only process the N most frequent unmapped tags.",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL_ID",
        default=None,
        help="With --build-aliases: model to use (defaults to LLM_MODEL / OPENROUTER_MODEL).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --build-aliases: report what would be sent without calling the model.",
    )
    args = parser.parse_args()
    if args.build_aliases:
        model = (
            args.model
            or os.environ.get("LLM_MODEL")
            or os.environ.get("OPENROUTER_MODEL", "gpt-oss-20b")
        )
        build_aliases(args.limit, args.dry_run, model)
        return
    if args.candidates:
        entries = [
            (name, meta.get("count", 0), meta.get("last_seen", ""))
            for name, meta in load_candidates().items()
            if meta.get("count", 0) >= args.min
        ]
        if not entries:
            print("Keine abgelehnten Vorschläge protokolliert.")
            return
        entries.sort(key=lambda e: (-e[1], e[0]))
        for name, count, last_seen in entries:
            print(f"{count:5d}  {name}  (zuletzt {last_seen})")
        print(f"\n{len(entries)} Vorschläge, {sum(c for _, c, _ in entries)} Nennungen")
        return
    if args.list:
        _print_vocabulary()
        return
    parser.print_help()


if __name__ == "__main__":
    main()
