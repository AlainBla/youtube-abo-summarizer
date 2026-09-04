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
import functools
import json
from datetime import datetime, timezone
from pathlib import Path

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
    args = parser.parse_args()
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
