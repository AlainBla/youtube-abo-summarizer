"""Internationalisation strings for HTML templates."""

STRINGS: dict[str, dict] = {
    "de": {
        "lang_attr": "de",
        "page_title": "YouTube Zusammenfassung",
        "contents": "Inhalt",
        "new_badge": "neu",
        "no_thumb": "Kein Thumbnail",
        "read_btn": "\u2713 Gelesen",
        "bookmark_btn": "\u2605 Merken",
        "skip": "\u00fcberspringen \u2193",
        "no_videos": "Keine neuen Videos im gew\u00e4hlten Zeitraum.",
        "transcript_ip_blocked": (
            "\u26a0 Kein Transkript \u2014 YouTube blockiert Transkript-Anfragen von "
            "dieser IP-Adresse (Rechenzentrum erkannt)."
        ),
        "transcript_rate_limited": (
            "\u26a0 Kein Transkript \u2014 YouTube hat die Anfrage abgelehnt "
            "(zu viele Anfragen in kurzer Zeit)."
        ),
        "transcript_country_blocked": (
            "\U0001f310 Kein Transkript \u2014 Video ist nur in bestimmten Regionen "
            "verf\u00fcgbar (Geoblocking)."
        ),
        "transcript_unavailable": "Kein Transkript f\u00fcr dieses Video verf\u00fcgbar.",
    },
    "en": {
        "lang_attr": "en",
        "page_title": "YouTube Summary",
        "contents": "Contents",
        "new_badge": "new",
        "no_thumb": "No thumbnail",
        "read_btn": "\u2713 Read",
        "bookmark_btn": "\u2605 Save",
        "skip": "skip \u2193",
        "no_videos": "No new videos in the selected time range.",
        "transcript_ip_blocked": (
            "\u26a0 No transcript \u2014 YouTube is blocking transcript requests from "
            "this IP address (data center detected)."
        ),
        "transcript_rate_limited": (
            "\u26a0 No transcript \u2014 YouTube rejected the request "
            "(too many requests in a short time)."
        ),
        "transcript_country_blocked": (
            "\U0001f310 No transcript \u2014 Video is only available in certain "
            "regions (geo-blocking)."
        ),
        "transcript_unavailable": "No transcript available for this video.",
    },
}

DEFAULT_LANG = "de"
SUPPORTED_LANGS = list(STRINGS.keys())


def get_strings(lang: str) -> dict:
    """Return i18n strings for the given language code, falling back to default."""
    return STRINGS.get(lang, STRINGS[DEFAULT_LANG])


def resolve_lang(lang: str | None) -> str:
    """Resolve and validate a language code, falling back to default."""
    if not lang:
        return DEFAULT_LANG
    if lang in STRINGS:
        return lang
    prefix = lang.split("-")[0].lower()
    if prefix in STRINGS:
        return prefix
    return DEFAULT_LANG
