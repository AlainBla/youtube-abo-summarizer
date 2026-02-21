# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A tool to summarize YouTube subscriptions (YouTube Abo = YouTube Abonnements/subscriptions in German). It uses the YouTube Data API via Google OAuth.

## Credentials and Authentication

- `client_secrets.json` — Google OAuth client credentials (desktop/installed app flow). **Never commit this file.** Add it to `.gitignore`.
- `token.pickle` — Cached OAuth token generated after the first successful authorization. **Never commit this file.** Add it to `.gitignore`.

The OAuth flow uses `google-auth-oauthlib` (installed app / `redirect_uri: http://localhost`). On first run, a browser window opens for authorization; subsequent runs reuse `token.pickle`.
