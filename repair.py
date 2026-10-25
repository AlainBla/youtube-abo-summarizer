#!/usr/bin/env python3
"""Repair missing or outdated transcripts and summaries in the store.

Scans all DB entries and:
  - Re-fetches transcripts whose file is missing (skips country_blocked).
  - Re-summarizes videos that have a transcript file but no summary file.
  - With --force-summarize, re-summarizes even if a summary already exists
    (useful for fixing bad output from a specific model run).
  - With --video ID [ID ...], restricts all operations to those video IDs.

Usage:
  # Repair everything that is missing
  python repair.py

  # Preview without making any changes
  python repair.py --dry-run

  # Re-summarize two specific videos (e.g. bad output found)
  python repair.py --force-summarize --video abc123xyz def456uvw

  # Re-summarize all videos that have a transcript (fresh run with new model)
  python repair.py --force-summarize

  # Repair broken markup in stored summaries: timestamp links, stray </p> (no LLM calls)
  python repair.py --fix-links --dry-run
  python repair.py --fix-links

  # Rewrite stored tags through the controlled vocabulary (no LLM calls)
  python repair.py --remap-tags --dry-run
  python repair.py --remap-tags

  # Fill in missing video durations (yt-dlp, then one batched API call per 50)
  python repair.py --fill-durations --dry-run
  python repair.py --fill-durations
"""

import argparse
import collections
import os
import re
import sys
import time

from dotenv import load_dotenv

import openrouter
import store
import tags as tag_vocab
import transcripts as tr
import ytdlp_meta

load_dotenv()

# Exit code signalling that this run changed stored content: a summary
# written, a summary file rewritten by --fix-links, or tags rewritten by
# --remap-tags. Any of those changes what the exported archive shows (tags
# feed its filters and chips), so collect.sh's cron wrapper should re-export
# on this the same way it does on collect.py's EXIT_NEW_VIDEOS. Kept equal to
# that constant (pinned by tests/test_repair_exit_code.py) but defined here
# rather than imported: openrouter (hence openai) is already on this module's
# import path, but collect.py additionally pulls in googleapiclient and
# google.auth for OAuth -- real weight to add just to read one integer off it.
EXIT_CONTENT_CHANGED = 10


def _exit_code(dry_run: bool, changed: bool) -> int:
    """A dry run writes nothing, so it always exits 0 -- even when it reports
    changes it would have made.
    """
    return EXIT_CONTENT_CHANGED if changed and not dry_run else 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Repair missing transcripts and summaries in the store."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes.",
    )
    parser.add_argument(
        "--video",
        metavar="ID,ID,...",
        help="Comma-separated list of video IDs to limit repairs to.",
    )
    parser.add_argument(
        "--force-summarize",
        action="store_true",
        help="Re-summarize even if a summary file already exists.",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Ignore SOCKS_PROXY_URL and WEBSHARE_PROXY_URL and fetch via a direct connection.",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL_ID",
        default=None,
        help="LLM model to use for summarization (overrides LLM_MODEL / OPENROUTER_MODEL env vars).",
    )
    parser.add_argument(
        "--fix-links",
        action="store_true",
        help="Repair broken markup in stored summaries (JSON-wrapped summaries, "
             "unclosed hrefs and tags, links showing a period instead of a timestamp, "
             "wrong t= offsets, broken "
             "anchor tags, missing ts-link class, </p> closed mid-sentence). "
             "No LLM calls. Combine with --dry-run.",
    )
    parser.add_argument(
        "--remap-tags",
        action="store_true",
        help="Rewrite stored tags through the controlled vocabulary in tags.py "
             "and tag_aliases.json. No LLM calls. Combine with --dry-run.",
    )
    parser.add_argument(
        "--fill-durations",
        action="store_true",
        help="Look up the duration of every stored video that has none "
             "(yt-dlp first, then one batched YouTube API call per 50). "
             "No LLM calls. Combine with --dry-run.",
    )
    return parser.parse_args()


def fix_links(entries, dry_run: bool) -> int:
    """Rewrite stored summaries through openrouter's timestamp-link repair.

    Purely textual: recomputes each t= from its visible label, closes anchors the
    model left open, adds the missing ts-link class, and drops </p> tags the
    model placed mid-sentence. No API calls.

    Returns the number of summaries that changed (or, under --dry-run, that
    would have changed) -- the caller feeds that into the exit-code decision.
    """
    n_changed = 0
    for entry in entries:
        path = store.SUMMARIES_DIR / f"{entry['video_id']}.html"
        if not path.exists():
            continue
        before = path.read_text(encoding="utf-8")
        after = openrouter.repair_summary_html(before)
        if after == before:
            continue
        n_changed += 1
        print(f"  [fix-links] {entry['title'][:60]}  ({entry['video_id']})")
        if not dry_run:
            path.write_text(after, encoding="utf-8")
    verb = "would be repaired" if dry_run else "repaired"
    print(f"\nDone.  {n_changed} of {len(entries)} summaries {verb}.")
    return n_changed


def remap_tags(entries, dry_run: bool) -> int:
    """Rewrite stored tags through the controlled vocabulary. No API calls.

    Rejected old tags are deliberately not logged as candidates: the log exists
    to show what the model asks for now, and 10 700 historical inventions would
    bury that signal.

    Returns the number of videos whose tags changed (or would change, under
    --dry-run) -- the caller feeds that into the exit-code decision.
    """
    changed = 0
    emptied = 0
    distribution: collections.Counter[str] = collections.Counter()
    for entry in entries:
        before = entry.get("tags") or []
        if not before:
            continue
        after, _rejected = tag_vocab.canonicalize(before)
        distribution.update(after)
        if after == before:
            continue
        changed += 1
        if not after:
            emptied += 1
            print(f"  [remap-tags] ohne Tag: {entry.get('title', '')[:60]}  ({entry['video_id']})")
        if not dry_run:
            store.update_tags(entry["video_id"], after)
    verb = "würden geändert" if dry_run else "geändert"
    print(f"\n{changed} von {len(entries)} Videos {verb}, davon {emptied} ohne Tag.")
    print(f"{len(distribution)} verschiedene Tags im Einsatz, häufigste:")
    for tag, count in distribution.most_common(15):
        print(f"  {count:5d}  {tag}")
    return changed


def _known_duration(duration: str | None) -> str | None:
    """The duration, or None for none at all or a zero length ("P0D", "PT0S").

    A stream that is still live reports zero; storing that would read as a
    short. An ISO 8601 duration is non-zero exactly when it has a non-zero digit.
    """
    return duration if duration and re.search(r"[1-9]", duration) else None


def _api_service():
    """The YouTube API service, or None when no token is usable without a browser.

    Imported here so the other repair modes do not pull in googleapiclient.
    """
    import youtube_client
    if not youtube_client.has_usable_token():
        return None
    return youtube_client.build_service()


def fill_durations(entries, dry_run: bool, no_proxy: bool = False, get_service=_api_service) -> int:
    """Look up the duration of every entry that has none. No LLM calls.

    Two kinds of entry end up here: rows restored by recover_from_export.py,
    which cannot carry an ISO duration, and live streams collected while they
    were still running, which had no length yet. yt-dlp first, then one
    videos().list per 50 for what it could not answer. A stream that is still
    live stays without one and is counted as such.

    Returns the number of durations found (written, unless dry_run).
    """
    todo = [e for e in entries if not e.get("duration")]
    print(f"{len(todo)} von {len(entries)} Videos ohne Dauer")
    found: dict[str, str] = {}
    missing = []
    for i, entry in enumerate(todo, 1):
        meta = ytdlp_meta.get_video_metadata(entry["video_id"], no_proxy=no_proxy)
        duration = _known_duration(meta.get("duration")) if meta else None
        if duration:
            found[entry["video_id"]] = duration
        else:
            missing.append(entry)
        if i % 25 == 0:
            print(f"  ... {i}/{len(todo)} per yt-dlp geprüft")

    if missing:
        try:
            service = get_service()
        except Exception as exc:  # noqa: BLE001 - a degraded run, not a crash
            print(f"  YouTube API nicht verfügbar: {exc}", file=sys.stderr)
            service = None
        if service is None:
            print(f"  {len(missing)} Video(s) bleiben ohne Dauer (kein API-Zugang)", file=sys.stderr)
        else:
            from youtube_client import get_video_durations
            try:
                durations = get_video_durations(service, [e["video_id"] for e in missing])
            except Exception as exc:  # noqa: BLE001 - HttpError and transport errors alike
                print(f"  Dauer-Abruf über die API fehlgeschlagen: {exc}", file=sys.stderr)
                durations = {}
            for entry in missing:
                duration = _known_duration(durations.get(entry["video_id"]))
                if duration:
                    found[entry["video_id"]] = duration

    for entry in todo:
        duration = found.get(entry["video_id"])
        if duration is None:
            print(f"  [fill-durations] weiterhin ohne Dauer: {entry.get('title', '')[:60]}  ({entry['video_id']})")
        elif not dry_run:
            store.update_duration(entry["video_id"], duration)
    verb = "würden ergänzt" if dry_run else "ergänzt"
    print(f"\n{len(found)} von {len(todo)} Dauern {verb}.")
    return len(found)


def main():
    args = parse_args()
    if not (args.fix_links or args.remap_tags or args.fill_durations):
        tr.log_proxy_config(no_proxy=args.no_proxy)
    model = args.model or os.environ.get("LLM_MODEL") or os.environ.get("OPENROUTER_MODEL", "gpt-oss-20b")
    video_filter = {v.strip() for v in args.video.split(",") if v.strip()} if args.video else None

    entries = store.get_all_videos(
        with_transcripts=not (args.remap_tags or args.fill_durations)
    )
    if video_filter:
        entries = [e for e in entries if e["video_id"] in video_filter]
        not_found = video_filter - {e["video_id"] for e in entries}
        for vid_id in sorted(not_found):
            print(f"Warning: {vid_id} not found in store, skipping.")

    if not entries:
        print("No videos to process.")
        return _exit_code(args.dry_run, changed=False)

    if args.remap_tags:
        print(f"Scanning {len(entries)} video(s) for tags outside the vocabulary")
        if args.dry_run:
            print("  (dry-run — no changes will be written)")
        changed = remap_tags(entries, args.dry_run)
        return _exit_code(args.dry_run, changed > 0)

    if args.fill_durations:
        if args.dry_run:
            print("  (dry-run — no changes will be written)")
        changed = fill_durations(entries, args.dry_run, no_proxy=args.no_proxy)
        return _exit_code(args.dry_run, changed > 0)

    if args.fix_links:
        print(f"Scanning {len(entries)} video(s) for broken summary markup")
        if args.dry_run:
            print("  (dry-run — no changes will be written)")
        changed = fix_links(entries, args.dry_run)
        return _exit_code(args.dry_run, changed > 0)

    print(f"Scanning {len(entries)} video(s)  [model: {model}]")
    if args.dry_run:
        print("  (dry-run — no changes will be written)")

    n_transcript_ok = 0
    n_transcript_fail = 0
    n_summarized = 0
    n_skipped = 0
    n_rejected = 0

    for entry in entries:
        vid_id    = entry["video_id"]
        vid_title = entry["title"]
        vid_channel = entry.get("channel_title")
        t_exists  = entry["transcript"] is not None
        s_exists  = entry["summary"] is not None
        t_error   = entry.get("transcript_error")

        # country_blocked is a permanent restriction — never re-fetch
        needs_transcript = not t_exists and t_error != "country_blocked"
        needs_summary    = not s_exists or args.force_summarize

        if not needs_transcript and not needs_summary:
            n_skipped += 1
            continue

        reasons = []
        if needs_transcript:
            reasons.append("transcript missing")
        if not s_exists:
            reasons.append("summary missing")
        elif args.force_summarize:
            reasons.append("force-summarize")

        print(f"\n  [{', '.join(reasons)}] {vid_title}  ({vid_id})")

        if args.dry_run:
            continue

        transcript = entry["transcript"]

        # ── Step 1: fetch transcript if missing ──────────────────────────────
        if needs_transcript:
            print(f"    Fetching transcript...")
            fetch_fn = tr.get_transcript_no_proxy if args.no_proxy else tr.get_transcript
            transcript, lang, t_error = fetch_fn(vid_id)
            time.sleep(2)
            if transcript:
                n_transcript_ok += 1
                store.update_video_with_summary(vid_id, transcript, None, t_error,
                                                transcript_lang=lang)
            else:
                print(f"    Still unavailable: {t_error or 'unavailable'}")
                store.update_video_with_summary(vid_id, None, None, t_error)
                n_transcript_fail += 1
                continue

        # ── Step 2: summarize ─────────────────────────────────────────────────
        if transcript is None:
            print(f"    No transcript available, cannot summarize.")
            n_skipped += 1
            continue

        if needs_summary or needs_transcript:
            print(f"    Summarizing via {model}...")
            llm_path = store.get_llm_transcript_path(vid_id)
            llm_input = llm_path.read_text(encoding="utf-8") if llm_path else transcript
            try:
                summary, tags = openrouter.summarize_video(vid_id, vid_title, llm_input, model,
                                                   channel=vid_channel)
            except openrouter.SummaryRejected as e:
                print(f"    Summary rejected: {e} — left unchanged.")
                n_rejected += 1
                continue
            store.update_video_with_summary(
                vid_id, None, summary, t_error, summary_model=model, tags=tags
            )
            n_summarized += 1

    print(
        f"\nDone.  transcripts: {n_transcript_ok} fetched / {n_transcript_fail} failed  |  "
        f"summaries: {n_summarized} written / {n_rejected} rejected  |  skipped: {n_skipped}"
    )
    return _exit_code(args.dry_run, n_summarized > 0)


if __name__ == "__main__":
    sys.exit(main())
