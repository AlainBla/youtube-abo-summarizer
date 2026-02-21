"""YouTube Data API v3 wrapper — auth, subscriptions, and video discovery."""

import os
import pickle
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRETS = os.path.join(os.path.dirname(__file__), "client_secrets.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.pickle")


def build_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("youtube", "v3", credentials=creds)


def get_subscribed_channels(service) -> list[dict]:
    """Return all subscribed channels as [{channel_id, title}]."""
    channels = []
    request = service.subscriptions().list(
        part="snippet",
        mine=True,
        maxResults=50,
    )
    while request:
        response = request.execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            channels.append(
                {
                    "channel_id": snippet["resourceId"]["channelId"],
                    "title": snippet["title"],
                }
            )
        request = service.subscriptions().list_next(request, response)
    return channels


def resolve_channel_id(service, identifier: str) -> dict | None:
    """Resolve a channel URL, handle, or bare ID to {channel_id, title}."""
    # Already a channel ID (UCxxx...)
    if identifier.startswith("UC") and len(identifier) == 24:
        resp = service.channels().list(part="snippet", id=identifier).execute()
    else:
        # Try as a handle or custom URL via search
        resp = service.search().list(
            part="snippet",
            q=identifier,
            type="channel",
            maxResults=1,
        ).execute()
        items = resp.get("items", [])
        if not items:
            return None
        channel_id = items[0]["snippet"]["channelId"]
        resp = service.channels().list(part="snippet", id=channel_id).execute()

    items = resp.get("items", [])
    if not items:
        return None
    snippet = items[0]["snippet"]
    return {"channel_id": items[0]["id"], "title": snippet["title"]}


def get_new_videos(service, channel_id: str, since: datetime) -> list[dict]:
    """Return videos published after `since` for a channel as [{video_id, title, published_at, thumbnail_url}]."""
    published_after = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    videos = []
    request = service.search().list(
        part="snippet",
        channelId=channel_id,
        type="video",
        order="date",
        publishedAfter=published_after,
        maxResults=50,
    )
    while request:
        response = request.execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            thumbnails = snippet.get("thumbnails", {})
            thumbnail_url = (
                thumbnails.get("medium", thumbnails.get("default", {})).get("url", "")
            )
            videos.append(
                {
                    "video_id": item["id"]["videoId"],
                    "title": snippet["title"],
                    "published_at": snippet["publishedAt"],
                    "thumbnail_url": thumbnail_url,
                }
            )
        request = service.search().list_next(request, response)
    return videos
