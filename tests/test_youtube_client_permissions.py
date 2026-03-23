"""Tests for secure token.pickle file permissions — H3 fix."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pickle
from unittest.mock import patch, MagicMock
import youtube_client


class _PicklableCreds:
    """Minimal picklable fake credentials object."""
    valid = True
    expired = False
    refresh_token = None


def test_token_file_written_with_mode_600(tmp_path, monkeypatch):
    """After saving OAuth credentials, token file must not be world-readable (0600)."""
    token_path = tmp_path / "token.pickle"
    monkeypatch.setattr(youtube_client, "TOKEN_FILE", str(token_path))

    fake_creds = _PicklableCreds()

    fake_flow = MagicMock()
    fake_flow.run_local_server.return_value = fake_creds

    with patch("youtube_client.InstalledAppFlow") as mock_flow_cls, \
         patch("youtube_client.build", return_value=MagicMock()):
        mock_flow_cls.from_client_secrets_file.return_value = fake_flow
        youtube_client.build_service()

    assert token_path.exists(), "token.pickle must be written"
    mode_octal = oct(token_path.stat().st_mode)[-3:]
    assert mode_octal == "600", (
        f"token.pickle must be owner-read/write only (0600), got {mode_octal}"
    )
