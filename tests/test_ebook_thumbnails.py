import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import ebook
from export_harness import video


def test_images_are_fetched_once_and_cached_on_disk(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return b"\xff\xd8jpegdata"

    videos = [video("v1", "2026-01-01T00:00:00Z")]
    images, failed = ebook.collect_thumbnails(videos, str(tmp_path), fetch=fetch)
    assert images["v1"] == b"\xff\xd8jpegdata" and failed == 0

    again, _ = ebook.collect_thumbnails(videos, str(tmp_path), fetch=fetch)
    assert again["v1"] == b"\xff\xd8jpegdata"
    assert len(calls) == 1, "second run must come from the cache"


def test_a_failing_download_is_counted_and_skipped(tmp_path):
    def fetch(url):
        raise OSError("timeout")

    images, failed = ebook.collect_thumbnails(
        [video("v1", "2026-01-01T00:00:00Z")], str(tmp_path), fetch=fetch)
    assert images == {} and failed == 1


def test_oversized_images_are_skipped(tmp_path):
    images, failed = ebook.collect_thumbnails(
        [video("v1", "2026-01-01T00:00:00Z")], str(tmp_path),
        fetch=lambda url: b"x" * 10, max_bytes=5)
    assert images == {} and failed == 1
    # An oversized blob must never be written to the cache -- otherwise a
    # later run would load the rejected payload from disk and serve it as
    # if it were a validated thumbnail.
    assert not (tmp_path / "v1.jpg").exists()


def test_an_empty_body_is_skipped(tmp_path):
    images, failed = ebook.collect_thumbnails(
        [video("v1", "2026-01-01T00:00:00Z")], str(tmp_path), fetch=lambda url: b"")
    assert images == {} and failed == 1
    assert not (tmp_path / "v1.jpg").exists()


def test_non_https_urls_are_never_fetched(tmp_path):
    v = video("v1", "2026-01-01T00:00:00Z", thumbnail_url="http://example.com/a.jpg")
    def fetch(url):
        raise AssertionError("must not be called")
    images, failed = ebook.collect_thumbnails([v], str(tmp_path), fetch=fetch)
    assert images == {} and failed == 1


def test_non_https_urls_never_reach_the_fetcher(tmp_path):
    # test_non_https_urls_are_never_fetched above proves this via an
    # AssertionError raised from fetch() -- but AssertionError is caught by
    # the same `except Exception` that handles real fetch failures, so that
    # test cannot actually fail if the scheme check moved after the fetch
    # call. This counts calls instead, so it fails regardless of how the
    # skip is implemented.
    calls = []
    v = video("v1", "2026-01-01T00:00:00Z", thumbnail_url="http://example.com/a.jpg")
    images, failed = ebook.collect_thumbnails(
        [v], str(tmp_path), fetch=lambda url: calls.append(url) or b"\xff\xd8x")
    assert calls == [] and images == {} and failed == 1
