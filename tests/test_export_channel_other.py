"""Channels with fewer than five videos share one "Andere"/"Other" filter entry.

groupChannels() decides the split (pure), channelMatches() applies it, so both
are pinned here without a DOM.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")


def _run(snippet: str) -> dict:
    script = extract_script(render_export([video("v1", "2026-01-01T00:00:00Z")]))
    out = run_node(script, snippet)
    return json.loads(out.strip().splitlines()[-1])


def _videos_js(counts: dict) -> str:
    vids = []
    for cid, n in counts.items():
        vids += [{"channel_id": cid, "channel_title": cid.title()} for _ in range(n)]
    return json.dumps(vids)


def test_split_at_five():
    data = _run(f"""
    var g = groupChannels({_videos_js({"big": 5, "small": 4, "alpha": 7, "one": 1})}, CHANNEL_MIN_VIDEOS);
    console.log(JSON.stringify({{listed: g.listed.map(c => [c.id, c.count]),
                                 other: g.other.map(c => [c.id, c.count])}}));
    """)
    assert data["listed"] == [["alpha", 7], ["big", 5]]  # sorted by title, 5 is enough
    assert data["other"] == [["one", 1], ["small", 4]]


def test_no_other_group_when_every_channel_is_large():
    data = _run(f"""
    var g = groupChannels({_videos_js({"a": 5, "b": 9})}, CHANNEL_MIN_VIDEOS);
    console.log(JSON.stringify({{other: g.other.length}}));
    """)
    assert data["other"] == 0


def test_channel_matches():
    data = _run("""
    smallChannels = new Set(['tiny']);
    var tiny = {channel_id: 'tiny'}, big = {channel_id: 'big'};
    console.log(JSON.stringify({
      all: [channelMatches('', tiny), channelMatches('', big)],
      other: [channelMatches(OTHER_CHANNELS, tiny), channelMatches(OTHER_CHANNELS, big)],
      exact: [channelMatches('big', big), channelMatches('big', tiny), channelMatches('tiny', tiny)]
    }));
    """)
    assert data["all"] == [True, True]
    assert data["other"] == [True, False]
    assert data["exact"] == [True, False, True]


def test_label_in_both_languages():
    data = _run("""
    console.log(JSON.stringify({de: otherChannelsLabel(I18N.de, 12),
                                en: otherChannelsLabel(I18N.en, 12)}));
    """)
    assert data == {"de": "Andere (12)", "en": "Other (12)"}
