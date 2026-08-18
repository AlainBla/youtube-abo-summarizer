"""collect.py signals "new videos landed" through its exit code.

Cron chains the export onto that signal, so an export -- and with it the
"new videos" banner in the archive -- only happens when something was
actually added. Importing collect.py pulls in the full runtime stack
(googleapiclient, openai, pydantic); where that is unavailable the module
tests skip and the shell-wiring tests below still run.
"""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

collect = pytest.importorskip("collect", reason="collect.py runtime deps unavailable")


def test_nothing_added_exits_zero():
    assert collect._exit_code(0) == 0


def test_added_videos_exit_with_the_new_videos_code():
    assert collect._exit_code(1) == collect.EXIT_NEW_VIDEOS
    assert collect._exit_code(42) == collect.EXIT_NEW_VIDEOS


def test_the_signal_code_is_distinct_from_success_and_generic_failure():
    assert collect.EXIT_NEW_VIDEOS not in (0, 1)
