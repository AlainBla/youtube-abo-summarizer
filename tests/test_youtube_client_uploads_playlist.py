"""The uploads playlist ID is derivable, so it must not cost a quota unit.

channels().list costs one unit per channel per run. With ~100 subscriptions
and a half-hourly collect that is ~4 800 units a day spent re-deriving a
constant: YouTube mints the uploads playlist as the channel ID with "UC"
swapped for "UU". Exhausting the quota this way is what takes the on-demand
ingest down with it.
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

yc = pytest.importorskip("youtube_client", reason="googleapiclient unavailable")


class _ExplodingService:
    def channels(self):
        raise AssertionError("channels().list must not be called for a UC... channel ID")


def test_the_uploads_playlist_is_derived_from_the_channel_id():
    channel_id = "UC" + "a" * 22
    assert yc.uploads_playlist_id(channel_id) == "UU" + "a" * 22


def test_deriving_spends_no_api_call():
    assert yc._get_uploads_playlist_id(_ExplodingService(), "UC" + "b" * 22) == "UU" + "b" * 22


def test_an_id_that_is_not_a_channel_id_is_not_derived():
    assert yc.uploads_playlist_id("") is None
    assert yc.uploads_playlist_id("UCshort") is None
    assert yc.uploads_playlist_id("PL" + "c" * 22) is None


def test_an_underivable_id_still_falls_back_to_the_api():
    class _Service:
        def channels(self):
            return self

        def list(self, **kwargs):
            return self

        def execute(self):
            return {
                "items": [
                    {"contentDetails": {"relatedPlaylists": {"uploads": "UUfallback"}}}
                ]
            }

    assert yc._get_uploads_playlist_id(_Service(), "legacy-handle") == "UUfallback"
