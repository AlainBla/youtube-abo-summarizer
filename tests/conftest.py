"""Project-level test configuration.

Sets env vars required by modules that read os.environ at import time
(send_mail.py, openrouter.py, etc.).
"""
import os
import sys

# Ensure the project root is on the import path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("SMTP_USER", "test@example.com")
os.environ.setdefault("SMTP_PASS", "testpass")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
