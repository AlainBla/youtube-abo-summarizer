"""Tests for SMTP port consistency in send_mail — M4 fix."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import patch, MagicMock, call
import importlib


def _make_smtp_mock():
    """Return a context-manager mock for smtplib.SMTP / SMTP_SSL."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_port_587_uses_starttls(tmp_path, monkeypatch):
    """Port 587 must use SMTP (STARTTLS), not SMTP_SSL."""
    import send_mail
    monkeypatch.setattr(send_mail, "SMTP_PORT", 587)

    html_file = tmp_path / "test.html"
    html_file.write_text("<html>test</html>", encoding="utf-8")

    smtp_ctx = _make_smtp_mock()
    ssl_ctx = _make_smtp_mock()

    with patch("send_mail.smtplib.SMTP", return_value=smtp_ctx) as mock_smtp, \
         patch("send_mail.smtplib.SMTP_SSL", return_value=ssl_ctx) as mock_ssl:
        send_mail.send("Subject", "to@example.com", str(html_file))

    mock_smtp.assert_called_once()          # plain SMTP used
    mock_ssl.assert_not_called()            # SMTP_SSL not used
    smtp_ctx.starttls.assert_called_once()  # STARTTLS negotiated


def test_port_465_uses_smtp_ssl(tmp_path, monkeypatch):
    """Port 465 must use SMTP_SSL (implicit TLS)."""
    import send_mail
    monkeypatch.setattr(send_mail, "SMTP_PORT", 465)

    html_file = tmp_path / "test.html"
    html_file.write_text("<html>test</html>", encoding="utf-8")

    smtp_ctx = _make_smtp_mock()
    ssl_ctx = _make_smtp_mock()

    with patch("send_mail.smtplib.SMTP", return_value=smtp_ctx) as mock_smtp, \
         patch("send_mail.smtplib.SMTP_SSL", return_value=ssl_ctx) as mock_ssl:
        send_mail.send("Subject", "to@example.com", str(html_file))

    mock_ssl.assert_called_once()   # SMTP_SSL used
    mock_smtp.assert_not_called()   # plain SMTP not used
    ssl_ctx.starttls.assert_not_called()  # no STARTTLS on implicit-TLS connection
