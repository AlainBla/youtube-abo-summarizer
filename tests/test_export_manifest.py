"""The export writes a manifest sidecar the open page can poll for updates."""
import builtins
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import renderer
from export_harness import extract_script, node_available, render_export, run_node, video


def _manifest_literal(html: str) -> dict:
    m = re.search(r"const MANIFEST = (\{.*?\});", html)
    assert m, "no MANIFEST literal in export HTML"
    return json.loads(m.group(1))


def _manifest_url(html: str) -> str:
    m = re.search(r'const MANIFEST_URL = "(.*?)";', html)
    assert m, "no MANIFEST_URL in export HTML"
    return m.group(1)


def _render_to(tmpdir: str, videos: list[dict], name: str = "archive.html", **kwargs) -> str:
    out = os.path.join(tmpdir, name)
    renderer.render_export_html(videos, out, **kwargs)
    return out


def test_sidecar_is_written_next_to_the_html(tmp_path):
    out = _render_to(str(tmp_path), [video("v1", "2026-01-01T00:00:00Z")])
    assert os.path.exists(out + ".meta.json")


def test_sidecar_counts_videos_and_names_the_newest(tmp_path):
    videos = [
        video("v1", "2026-01-01T00:00:00Z"),
        video("v3", "2026-03-01T00:00:00Z"),
        video("v2", "2026-02-01T00:00:00Z"),
    ]
    out = _render_to(str(tmp_path), videos)
    with open(out + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["video_count"] == 3
    assert meta["newest_id"] == "v3"
    assert meta["newest_published_at"] == "2026-03-01T00:00:00Z"
    assert meta["generated_at"]


def test_embedded_manifest_matches_the_sidecar(tmp_path):
    out = _render_to(str(tmp_path), [video("v1", "2026-01-01T00:00:00Z")])
    with open(out, encoding="utf-8") as f:
        html = f.read()
    with open(out + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert _manifest_literal(html) == meta


def test_recent_ids_lead_with_the_latest_arrival_not_the_latest_upload(tmp_path):
    # "New" means new to this archive: a video backfilled today leads the
    # window even though it was published years ago, because that is what the
    # page counts as an arrival (and what the added-desc sort already means).
    videos = [
        video("old", "2020-01-01T00:00:00Z", collected_at="2026-03-01T00:00:00Z"),
        video("new", "2026-02-01T00:00:00Z", collected_at="2026-02-01T00:00:00Z"),
    ]
    out = _render_to(str(tmp_path), videos)
    with open(out + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["recent_ids"] == ["old", "new"]
    # ... while the archive itself is still ordered by publish date.
    assert meta["newest_id"] == "new"


def test_recent_ids_fall_back_to_published_at_without_collected_at(tmp_path):
    videos = [
        video("v1", "2026-01-01T00:00:00Z"),
        video("v3", "2026-03-01T00:00:00Z"),
        video("v2", "2026-02-01T00:00:00Z"),
    ]
    out = _render_to(str(tmp_path), videos)
    with open(out + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["recent_ids"] == ["v3", "v2", "v1"]


def test_recent_ids_are_capped_so_the_sidecar_stays_small(tmp_path):
    count = renderer.EXPORT_RECENT_IDS + 10
    videos = [
        video(
            "v%03d" % n,
            "2026-01-01T00:00:00Z",
            collected_at=(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=n))
            .isoformat(),
        )
        for n in range(count)
    ]
    out = _render_to(str(tmp_path), videos)
    with open(out + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert len(meta["recent_ids"]) == renderer.EXPORT_RECENT_IDS
    assert meta["recent_ids"][0] == "v%03d" % (count - 1)


def test_manifest_url_is_the_html_basename_plus_suffix(tmp_path):
    out = _render_to(str(tmp_path), [video("v1", "2026-01-01T00:00:00Z")], name="full_archive.html")
    with open(out, encoding="utf-8") as f:
        html = f.read()
    assert _manifest_url(html) == "full_archive.html.meta.json"


def test_empty_export_still_yields_a_valid_manifest(tmp_path):
    out = _render_to(str(tmp_path), [])
    with open(out + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["video_count"] == 0
    assert meta["newest_id"] is None
    assert meta["summary_count"] == 0
    assert isinstance(meta["summary_digest"], str) and meta["summary_digest"]


# ── summary_count / summary_digest ───────────────────────────────────────────


def _meta(tmp_path, videos, name="archive.html"):
    out = _render_to(str(tmp_path), videos, name=name)
    with open(out + ".meta.json", encoding="utf-8") as f:
        return json.load(f)


def test_summary_count_ignores_videos_without_a_summary(tmp_path):
    videos = [
        video("v1", "2026-01-01T00:00:00Z"),
        video("v2", "2026-02-01T00:00:00Z", summary=None),
        video("v3", "2026-03-01T00:00:00Z", summary=""),
    ]
    meta = _meta(tmp_path, videos)
    assert meta["summary_count"] == 1


def test_summary_fields_are_also_in_the_embedded_manifest(tmp_path):
    out = _render_to(str(tmp_path), [video("v1", "2026-01-01T00:00:00Z")])
    with open(out, encoding="utf-8") as f:
        html = f.read()
    with open(out + ".meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    embedded = _manifest_literal(html)
    assert embedded["summary_count"] == meta["summary_count"] == 1
    assert embedded["summary_digest"] == meta["summary_digest"]


def test_digest_is_stable_across_reruns_of_the_same_videos(tmp_path):
    videos = [video("v1", "2026-01-01T00:00:00Z"), video("v2", "2026-02-01T00:00:00Z")]
    first = _meta(tmp_path, videos, name="a.html")
    second = _meta(tmp_path, videos, name="b.html")
    assert first["summary_digest"] == second["summary_digest"]


def test_digest_changes_when_a_summary_is_edited(tmp_path):
    base = [video("v1", "2026-01-01T00:00:00Z"), video("v2", "2026-02-01T00:00:00Z")]
    before = _meta(tmp_path, base, name="before.html")
    edited = [
        video("v1", "2026-01-01T00:00:00Z", summary="<p>Rewritten</p>"),
        video("v2", "2026-02-01T00:00:00Z"),
    ]
    after = _meta(tmp_path, edited, name="after.html")
    assert before["summary_digest"] != after["summary_digest"]


def test_digest_changes_when_a_summary_is_removed(tmp_path):
    base = [video("v1", "2026-01-01T00:00:00Z"), video("v2", "2026-02-01T00:00:00Z")]
    before = _meta(tmp_path, base, name="before.html")
    dropped = [
        video("v1", "2026-01-01T00:00:00Z", summary=None),
        video("v2", "2026-02-01T00:00:00Z"),
    ]
    after = _meta(tmp_path, dropped, name="after.html")
    assert before["summary_digest"] != after["summary_digest"]
    assert before["summary_count"] == 2
    assert after["summary_count"] == 1


def test_digest_changes_when_a_summary_moves_to_a_different_video(tmp_path):
    """A record keyed only by summary text would miss a summary reassigned
    between videos with identical text elsewhere -- keying by video_id too
    catches it."""
    same_text = "<p>Shared text</p>"
    base = [
        video("v1", "2026-01-01T00:00:00Z", summary=same_text),
        video("v2", "2026-02-01T00:00:00Z", summary=None),
    ]
    before = _meta(tmp_path, base, name="before.html")
    moved = [
        video("v1", "2026-01-01T00:00:00Z", summary=None),
        video("v2", "2026-02-01T00:00:00Z", summary=same_text),
    ]
    after = _meta(tmp_path, moved, name="after.html")
    assert before["summary_digest"] != after["summary_digest"]
    assert before["summary_count"] == after["summary_count"] == 1


def test_html_is_written_before_the_sidecar(tmp_path, monkeypatch):
    """Order matters: a manifest ahead of its HTML would announce videos the
    served page does not contain yet."""
    opened: list[str] = []
    real_open = builtins.open

    def spy(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if isinstance(path, str) and "w" in str(mode):
            if path.endswith(".html") or path.endswith(".meta.json"):
                opened.append(os.path.basename(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    _render_to(str(tmp_path), [video("v1", "2026-01-01T00:00:00Z")])
    assert opened == ["archive.html", "archive.html.meta.json"]


# ── Browser-side polling ─────────────────────────────────────────────────────

GENERIC_UPDATE_DE = "Archiv aktualisiert"

POLL_SETUP = """
globalThis.__fetched = [];
function stubManifest(meta) {
  globalThis.fetch = function (url, opts) {
    __fetched.push({url: url, opts: opts});
    return Promise.resolve({
      ok: true, status: 200,
      json: function () { return Promise.resolve(meta); }
    });
  };
}
"""


def _poll_script(videos=None):
    html = render_export(videos or [video("v1", "2026-01-01T00:00:00Z")])
    return POLL_SETUP + "\n" + extract_script(html)


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_newer_manifest_shows_the_banner_with_the_new_video_count():
    script = _poll_script()
    snippet = """
    stubManifest({generated_at: '2099-01-01T00:00:00+00:00', video_count: MANIFEST.video_count + 3});
    checkForUpdate().then(function () {
      var banner = document.getElementById('update-banner');
      console.log(JSON.stringify({
        display: banner.style.display,
        text: document.getElementById('update-text').textContent,
        url: __fetched[0].url,
        cache: __fetched[0].opts.cache
      }));
      process.exit(0);
    });
    """
    out = json.loads(run_node(script, snippet))
    assert out["display"] != "none"
    assert "3" in out["text"]
    assert out["url"] == "export.html.meta.json"
    assert out["cache"] == "no-store"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_an_unchanged_manifest_leaves_the_banner_hidden():
    script = _poll_script()
    snippet = """
    stubManifest({generated_at: MANIFEST.generated_at, video_count: MANIFEST.video_count});
    checkForUpdate().then(function () {
      console.log(document.getElementById('update-banner').style.display);
      process.exit(0);
    });
    """
    assert run_node(script, snippet).strip() == "none"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_newer_export_without_more_videos_shows_the_generic_text():
    script = _poll_script()
    snippet = """
    stubManifest({generated_at: '2099-01-01T00:00:00+00:00', video_count: MANIFEST.video_count});
    checkForUpdate().then(function () {
      console.log(JSON.stringify({
        display: document.getElementById('update-banner').style.display,
        text: document.getElementById('update-text').textContent
      }));
      process.exit(0);
    });
    """
    out = json.loads(run_node(script, snippet))
    assert out["display"] != "none"
    assert out["text"] == GENERIC_UPDATE_DE


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_dismiss_silences_that_version_but_not_the_next_one():
    script = _poll_script()
    snippet = """
    stubManifest({generated_at: '2099-01-01T00:00:00+00:00', video_count: MANIFEST.video_count + 1});
    checkForUpdate().then(function () {
      dismissUpdate();
      var afterDismiss = document.getElementById('update-banner').style.display;
      return checkForUpdate().then(function () {
        var stillHidden = document.getElementById('update-banner').style.display;
        stubManifest({generated_at: '2099-06-01T00:00:00+00:00', video_count: MANIFEST.video_count + 2});
        return checkForUpdate().then(function () {
          console.log(JSON.stringify({
            afterDismiss: afterDismiss,
            stillHidden: stillHidden,
            afterNewer: document.getElementById('update-banner').style.display
          }));
          process.exit(0);
        });
      });
    });
    """
    out = json.loads(run_node(script, snippet))
    assert out["afterDismiss"] == "none"
    assert out["stillHidden"] == "none"
    assert out["afterNewer"] != "none"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_no_polling_for_a_local_file_archive():
    script = _poll_script()
    snippet = """
    location.protocol = 'file:';
    checkForUpdate().then(function () {
      console.log(JSON.stringify({fetches: __fetched.length}));
      process.exit(0);
    });
    """
    out = json.loads(run_node(script, snippet))
    assert out["fetches"] == 0


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_the_banner_text_follows_the_language_switch():
    script = _poll_script()
    snippet = """
    stubManifest({generated_at: '2099-01-01T00:00:00+00:00', video_count: MANIFEST.video_count + 2});
    checkForUpdate().then(function () {
      var de = document.getElementById('update-text').textContent;
      applyLang('en');
      console.log(JSON.stringify({
        de: de,
        en: document.getElementById('update-text').textContent,
        btn: document.getElementById('update-reload').textContent,
        dismissLabel: document.getElementById('update-dismiss').getAttribute('aria-label')
      }));
      process.exit(0);
    });
    """
    out = json.loads(run_node(script, snippet))
    assert "neue" in out["de"].lower()
    assert "new" in out["en"].lower()
    assert out["btn"].lower() in ("reload", "neu laden")
    assert out["dismissLabel"] == "Dismiss"
