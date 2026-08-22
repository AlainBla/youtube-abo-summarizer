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


def test_non_https_urls_are_never_fetched(tmp_path):
    v = video("v1", "2026-01-01T00:00:00Z", thumbnail_url="http://example.com/a.jpg")
    def fetch(url):
        raise AssertionError("must not be called")
    images, failed = ebook.collect_thumbnails([v], str(tmp_path), fetch=fetch)
    assert images == {} and failed == 1
