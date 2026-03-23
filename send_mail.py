#!/usr/bin/env python3
"""Send an HTML file as an email via a configured SMTP server."""

import os
import smtplib
import sys
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)


def send(subject: str, to: str, html_file: str) -> None:
    with open(html_file, encoding="utf-8") as f:
        html = f.read()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg.set_content("This email requires an HTML-capable mail client.")
    msg.add_alternative(html, subtype="html")

    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
    print(f"Mail sent to {to} via {SMTP_HOST}:{SMTP_PORT}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: send_mail.py <subject> <to> <html_file>")
        sys.exit(1)
    send(subject=sys.argv[1], to=sys.argv[2], html_file=sys.argv[3])
