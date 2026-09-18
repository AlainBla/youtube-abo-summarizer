"""Waiting for a freshly queued video to turn up in the archive.

The manifest only says *that* a new export happened, never which video it
brought, so the page reloads into ?v=ID and lets the deep-link view decide.
That makes two things critical: the saved baseline must be re-pinned to the
export currently being viewed (otherwise every poll reloads again), and a
video that is already in the archive must not be waited for at all.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")

SYNC = "https://sync.example.com"


def _archive(n=60):
    return [video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(n)]


def _script(videos=None, **kw):
    kw.setdefault("sync_url", SYNC)
    return extract_script(render_export(videos or _archive(), **kw))


def _run(snippet, videos=None, prelude=""):
    return run_node(prelude + _script(videos), snippet)


def _json(snippet, videos=None, prelude=""):
    return json.loads(_run(snippet, videos, prelude))


# ── the decision ──────────────────────────────────────────────────────────────

def test_an_unchanged_manifest_is_not_a_reason_to_reload():
    out = _json("""
      var saved = {video_id: 'v001', generated_at: MANIFEST.generated_at, started: Date.now()};
      console.log(JSON.stringify(ingestWaitReady(saved, MANIFEST)));
      process.exit(0);
    """)
    assert out is False


def test_a_new_export_is():
    out = _json("""
      var saved = {video_id: 'v001', generated_at: '2020-01-01T00:00:00Z', started: Date.now()};
      console.log(JSON.stringify(ingestWaitReady(saved, MANIFEST)));
      process.exit(0);
    """)
    assert out is True


def test_a_manifest_without_a_timestamp_decides_nothing():
    out = _json("""
      var saved = {video_id: 'v001', generated_at: 'x', started: Date.now()};
      console.log(JSON.stringify([ingestWaitReady(saved, {}), ingestWaitReady(null, MANIFEST)]));
      process.exit(0);
    """)
    assert out == [False, False]


def test_the_wait_gives_up_after_ten_minutes():
    out = _json("""
      var now = Date.now();
      console.log(JSON.stringify([
        ingestWaitExpired({started: now - 9 * 60 * 1000}, now),
        ingestWaitExpired({started: now - 11 * 60 * 1000}, now)
      ]));
      process.exit(0);
    """)
    assert out == [False, True]


# ── the reload ────────────────────────────────────────────────────────────────

def test_a_new_export_sends_the_page_to_that_video():
    out = _run("""
      saveIngestWait({video_id: 'abc12345678', generated_at: 'older', started: Date.now()});
      globalThis.fetch = function () {
        return Promise.resolve({ok: true, json: function () {
          return Promise.resolve({generated_at: 'newer'});
        }});
      };
      pollIngestWait().then(function () {
        console.log(location.href);
        process.exit(0);
      });
    """)
    assert out.strip() == "https://example.com/x.html?v=abc12345678"


def test_an_unchanged_manifest_leaves_the_page_where_it_is():
    out = _json("""
      var href = location.href;
      saveIngestWait({video_id: 'abc12345678', generated_at: MANIFEST.generated_at, started: Date.now()});
      globalThis.fetch = function () {
        return Promise.resolve({ok: true, json: function () {
          return Promise.resolve(MANIFEST);
        }});
      };
      pollIngestWait().then(function () {
        console.log(JSON.stringify({moved: location.href !== href, kept: !!loadIngestWait()}));
        process.exit(0);
      });
    """)
    assert out == {"moved": False, "kept": True}


def test_resuming_re_pins_the_baseline_to_the_export_being_viewed():
    """Without this the very next poll sees a manifest that differs from the
    stale saved value and reloads again -- forever."""
    out = _json("""
      saveIngestWait({video_id: 'v003', generated_at: 'ancient', started: Date.now()});
      resumeIngestWait();
      console.log(JSON.stringify(loadIngestWait().generated_at === MANIFEST.generated_at));
      process.exit(0);
    """)
    assert out is True


# ── arriving, and not arriving ────────────────────────────────────────────────

def test_the_wait_ends_when_the_video_is_on_screen():
    out = _json(
        """
        setTimeout(function () {
          console.log(JSON.stringify({pending: !!loadIngestWait()}));
          process.exit(0);
        }, 400);
        """,
        prelude=("location.search = '?v=v003';\n"
                 "globalThis.localStorage.setItem('yt_ingest_wait', JSON.stringify("
                 "{video_id: 'v003', generated_at: 'ancient', started: Date.now()}));\n"),
    )
    assert out == {"pending": False}


def test_a_reload_that_did_not_bring_the_video_keeps_waiting():
    out = _json(
        """
        setTimeout(function () {
          var saved = loadIngestWait();
          console.log(JSON.stringify({pending: !!saved, id: saved && saved.video_id}));
          process.exit(0);
        }, 400);
        """,
        prelude=("location.search = '?v=zzz11111111';\n"
                 "globalThis.localStorage.setItem('yt_ingest_wait', JSON.stringify("
                 "{video_id: 'zzz11111111', generated_at: 'ancient', started: Date.now()}));\n"),
    )
    assert out == {"pending": True, "id": "zzz11111111"}


# ── a video that is already there ─────────────────────────────────────────────

def test_a_video_already_in_the_archive_needs_no_queueing():
    out = _json("""
      setTimeout(function () {
        ingestTargetKnown('v003').then(function (known) {
          console.log(JSON.stringify(known));
          process.exit(0);
        });
      }, 300);
    """)
    assert out is True


def test_a_video_in_the_archive_without_a_summary_is_worth_queueing():
    videos = _archive(30)
    videos[5]["summary"] = None
    videos[5]["transcript_error"] = "ip_blocked"
    out = _json("""
      setTimeout(function () {
        ingestTargetKnown('v005').then(function (known) {
          console.log(JSON.stringify(known));
          process.exit(0);
        });
      }, 300);
    """, videos=videos)
    assert out is False


def test_an_unknown_video_is_worth_queueing():
    out = _json("""
      setTimeout(function () {
        ingestTargetKnown('zzz11111111').then(function (known) {
          console.log(JSON.stringify(known));
          process.exit(0);
        });
      }, 300);
    """)
    assert out is False


def test_a_submission_before_the_index_is_decoded_still_sees_the_archive():
    """The sync boot reveals the Ingest box from /api/whoami, before the data
    blob is parsed -- and the userscript submits the moment it appears. Judging
    "not in the archive" then would queue videos that are already there."""
    # doIngest() only accepts a real 11-character ID, so the archive needs one.
    videos = _archive(30) + [video("abcdefgh123", "2026-02-01T00:00:00Z")]
    out = _json("""
      var posted = [];
      var realFetch = globalThis.fetch;
      globalThis.fetch = function (url, opts) {
        if (String(url).indexOf('/api/ingest') !== -1) posted.push(url);
        return realFetch(url, opts);
      };
      document.getElementById('ingest-input').value = 'abcdefgh123';
      doIngest();                       // before bootstrap() has run
      setTimeout(function () {
        console.log(JSON.stringify({posted: posted.length, href: location.href}));
        process.exit(0);
      }, 500);
    """, videos=videos)
    assert out["posted"] == 0
    assert out["href"].endswith("?v=abcdefgh123")


def test_a_file_url_archive_is_not_offered_a_wait_it_cannot_carry_out():
    """No server, no manifest to poll: the wait could only ever time out."""
    out = _json("""
      location.protocol = 'file:';
      showWaitOffer('abc12345678');
      console.log(JSON.stringify(document.getElementById('ingest-wait-btn').style.display || 'never shown'));
      process.exit(0);
    """)
    assert out == "never shown"


def test_an_expired_wait_is_not_silently_given_another_ten_minutes():
    out = _json("""
      saveIngestWait({video_id: 'zzz11111111', generated_at: 'ancient',
                      started: Date.now() - 11 * 60 * 1000});
      resumeIngestWait();
      var saved = loadIngestWait();
      console.log(JSON.stringify({
        polling: waitTimer !== null,
        started_moved: saved.started > Date.now() - 60 * 1000
      }));
      process.exit(0);
    """)
    assert out == {"polling": False, "started_moved": False}


def test_both_languages_carry_the_wait_strings():
    html = render_export(_archive(3), sync_url=SYNC)
    for key in ("waitBtn", "waitRunning", "waitArrived", "waitTimeout", "alreadyInArchive"):
        assert html.count(key + ":") == 2, f"{key} missing from one of the JS I18N dicts"
