"""Turn selected videos into the files of an EPUB 3 archive."""

from datetime import date, datetime, timedelta


def _published_date(entry):
    """The publish date as a plain date; ISO strings may end in 'Z'."""
    raw = (entry.get("published_at") or "")[:19]
    return datetime.fromisoformat(raw).date()


def group_by_week(videos):
    """Group videos into ISO calendar weeks, oldest week first.

    The key is (iso_year, iso_week), never the week number alone: ISO week 1
    can start in December, so two different "week 1"s would otherwise merge.
    """
    buckets = {}
    for v in videos:
        d = _published_date(v)
        iso_year, iso_week, iso_weekday = d.isocalendar()
        key = (iso_year, iso_week)
        bucket = buckets.setdefault(key, {
            "iso_year": iso_year,
            "iso_week": iso_week,
            "start": d - timedelta(days=iso_weekday - 1),
            "end": d + timedelta(days=7 - iso_weekday),
            "anchor": "w-%04d-%02d" % (iso_year, iso_week),
            "videos": [],
        })
        bucket["videos"].append(v)

    weeks = [buckets[k] for k in sorted(buckets)]
    for w in weeks:
        w["videos"].sort(key=lambda v: (v.get("published_at") or "", v.get("video_id") or ""))
    return weeks
