"""YouTube Data API v3 wrapper — auth, subscriptions, and video discovery."""

import os
import pickle
import time
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRETS = os.path.join(os.path.dirname(__file__), "client_secrets.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.pickle")


def build_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            # trusted local file: written only by this app's own pickle.dump() below
            with open(TOKEN_FILE, "rb") as f:
                creds = pickle.load(f)
        except (EOFError, pickle.UnpicklingError):
            print(f"[youtube_client] {TOKEN_FILE} is corrupt (likely a truncated write); re-authenticating")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(port=0)
        tmp_file = TOKEN_FILE + ".tmp"
        with open(tmp_file, "wb") as f:
            pickle.dump(creds, f)
        os.chmod(tmp_file, 0o600)
        os.replace(tmp_file, TOKEN_FILE)

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


def get_video_durations(service, video_ids: list[str]) -> dict[str, str]:
    """Return {video_id: duration} for the given IDs.

    Duration is an ISO 8601 string like "PT1H2M3S". Videos not found are omitted.
    Batches requests at 50 IDs per call to stay within the API limit.
    """
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        for attempt in range(3):
            try:
                resp = service.videos().list(
                    part="contentDetails",
                    id=",".join(batch),
                ).execute()
                for item in resp.get("items", []):
                    result[item["id"]] = item["contentDetails"]["duration"]
                break
            except HttpError as e:
                if e.resp.status >= 500 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
    return result


def get_video_by_id(service, video_id: str) -> dict | None:
    """Fetch video metadata by ID. Returns dict with video details or None if not found."""
    for attempt in range(3):
        try:
            resp = service.videos().list(
                part="snippet,contentDetails",
                id=video_id,
            ).execute()
            items = resp.get("items", [])
            if not items:
                return None
            item = items[0]
            snippet = item["snippet"]
            thumbnails = snippet.get("thumbnails", {})
            thumbnail_url = (
                thumbnails.get("medium", thumbnails.get("default", {})).get("url", "")
            )
            return {
                "video_id": video_id,
                "title": snippet["title"],
                "published_at": snippet["publishedAt"],
                "thumbnail_url": thumbnail_url,
                "duration": item["contentDetails"]["duration"],
                "channel_id": snippet["channelId"],
                "channel_title": snippet["channelTitle"],
            }
        except HttpError as e:
            if e.resp.status >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
    return None


def _get_uploads_playlist_id(service, channel_id: str) -> str | None:
    """Return the uploads playlist ID for a channel (costs 1 quota unit)."""
    resp = service.channels().list(
        part="contentDetails",
        id=channel_id,
    ).execute()
    items = resp.get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def get_new_videos(service, channel_id: str, since: datetime) -> list[dict]:
    """Return videos published after `since` for a channel.

    Uses playlistItems.list (1 quota unit/page) instead of search.list
    (100 quota units/page) to stay within the daily 10,000-unit quota.
    """
    uploads_playlist_id = _get_uploads_playlist_id(service, channel_id)
    if not uploads_playlist_id:
        return []

    since_utc = since.astimezone(timezone.utc)
    videos = []

    request = service.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist_id,
        maxResults=50,
    )
    while request:
        response = request.execute()
        stop_early = False
        for item in response.get("items", []):
            snippet = item["snippet"]
            published_str = snippet.get("publishedAt", "")
            try:
                published_dt = datetime.fromisoformat(
                    published_str.replace("Z", "+00:00")
                )
            except ValueError:
                continue

            # Playlist items are newest-first; stop once we go past `since`
            if published_dt <= since_utc:
                stop_early = True
                break

            # Skip private/deleted videos (resourceId may lack videoId)
            resource = snippet.get("resourceId", {})
            if resource.get("kind") != "youtube#video":
                continue
            video_id = resource.get("videoId")
            if not video_id:
                continue

            thumbnails = snippet.get("thumbnails", {})
            thumbnail_url = (
                thumbnails.get("medium", thumbnails.get("default", {})).get("url", "")
            )
            videos.append(
                {
                    "video_id": video_id,
                    "title": snippet["title"],
                    "published_at": published_str,
                    "thumbnail_url": thumbnail_url,
                }
            )

        if stop_early:
            break
        request = service.playlistItems().list_next(request, response)

    return videos
