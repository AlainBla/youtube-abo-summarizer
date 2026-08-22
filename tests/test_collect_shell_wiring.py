"""The cron scripts must react to collect.py's exit codes correctly.

These are text assertions on the shell scripts -- crude, but they catch the
two ways this wiring silently breaks: collect.sh exporting unconditionally
(which would flip the archive's generated_at every run and leave the update
banner permanently on), and ingest_worker.sh treating the "new videos" code
as a failure and re-queueing the video forever.
"""
import os

REPO = os.path.dirname(os.path.dirname(__file__))


def _read(name: str) -> str:
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def test_collect_sh_exports_only_on_the_new_videos_code():
    sh = _read("collect.sh")
    assert "export.py" in sh, "collect.sh no longer runs the export"
    export_line = [ln for ln in sh.splitlines() if "export.py" in ln][0]
    gate = sh[: sh.index(export_line)]
    assert "EXIT_NEW_VIDEOS=10" in gate, "collect.sh must pin the signal code it gates on"
    assert '-eq "$EXIT_NEW_VIDEOS"' in gate or "-eq 10" in gate, \
        "the export must be gated on the new-videos exit code, not run unconditionally"


def test_collect_sh_does_not_abort_on_the_new_videos_code():
    sh = _read("collect.sh")
    collect_line = [ln for ln in sh.splitlines() if "collect.py" in ln and "python" in ln][0]
    # Under `set -e` an unguarded non-zero exit kills the script before the export.
    assert "|| rc=$?" in collect_line


def test_ingest_worker_accepts_the_new_videos_code_as_success():
    sh = _read("ingest_worker.sh")
    assert "EXIT_NEW_VIDEOS=10" in sh, "ingest_worker.sh must pin the signal code"
    assert '-eq "$EXIT_NEW_VIDEOS"' in sh or "-eq 10" in sh, \
        "ingest_worker.sh would re-queue a video whose collect run added it"


def test_no_host_specific_values_are_hardcoded_in_the_cron_scripts():
    """This repository is public: sync URL, output path and digest recipient
    belong in cron.env (gitignored), not in the tracked shell scripts."""
    import glob
    import re

    offenders = []
    for path in sorted(glob.glob(os.path.join(REPO, "*.sh"))):
        text = _read(os.path.basename(path))
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # A concrete host or mail address, as opposed to example.com in docs
            if re.search(r"https?://(?!sync\.example\.com)[\w.-]+\.(net|de|com|org)", line) \
                    or re.search(r"[\w.-]+@(?!example\.com)[\w.-]+\.(net|de|com|org)", line):
                offenders.append(f"{os.path.basename(path)}:{lineno}: {line.strip()}")
    assert not offenders, "host-specific values must live in cron.env:\n" + "\n".join(offenders)


def test_cron_env_is_gitignored_and_has_an_example():
    with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
        assert "cron.env" in f.read().splitlines()
    assert os.path.exists(os.path.join(REPO, "cron.env.example"))


def test_both_export_scripts_warn_when_sync_url_is_unset():
    """A missing SYNC_URL costs the archive its entire sync UI -- login,
    account display and the ingest button all vanish -- and the export
    otherwise succeeds without a word. It has to say so in the log."""
    for name in ("collect.sh", "ingest_worker.sh"):
        sh = _read(name)
        assert 'if [ -z "$SYNC_URL" ]' in sh, name
        assert "WARNING: SYNC_URL is unset" in sh, name
