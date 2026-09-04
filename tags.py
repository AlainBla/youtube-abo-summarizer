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
    args = parser.parse_args()
    if args.list:
        _print_vocabulary()
        return
    parser.print_help()


if __name__ == "__main__":
    main()
