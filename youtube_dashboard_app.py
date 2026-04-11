"""Local YouTube public-data multi-account dashboard server."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, unquote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

YOUTUBE_DATA_BASE = "https://www.googleapis.com/youtube/v3"
CACHE_TTL_SECONDS = 15 * 60
REPORTING_TIMEZONE = ZoneInfo("Asia/Shanghai")
SNAPSHOT_DIRNAME = "snapshots"


class DashboardError(RuntimeError):
    """Raised when dashboard data cannot be fetched."""


def load_dotenv_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_env_values(base_dir: Path) -> dict[str, str]:
    candidates = [
        Path(".env"),
        base_dir / ".env",
    ]
    merged: dict[str, str] = {}
    for candidate in candidates:
        try:
            if candidate.exists():
                merged.update(load_dotenv_file(candidate))
        except OSError:
            continue
    return merged


def sanitize_account_id(label: str) -> str:
    chars: list[str] = []
    last_dash = False
    for char in label.lower().strip():
        if char.isalnum():
            chars.append(char)
            last_dash = False
        elif not last_dash:
            chars.append("-")
            last_dash = True
    result = "".join(chars).strip("-")
    return result or "account"


def normalize_handle(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if "youtube.com/" in text:
        parsed = urlparse(text)
        path = parsed.path.strip("/")
        if path.startswith("@"):
            return path
        if "/@" in parsed.path:
            return f"@{parsed.path.split('/@', 1)[1].split('/')[0]}"
    return text if text.startswith("@") else f"@{text}"


def normalize_account_spec(item: dict[str, Any]) -> dict[str, str] | None:
    account_id = str(item.get("id", "")).strip()
    handle = normalize_handle(str(item.get("handle", "")).strip())
    alias = str(item.get("alias", "")).strip()
    label = str(item.get("label", "")).strip()
    display_name = alias or label or handle or account_id
    if account_id and handle and display_name:
        result = {
            "id": account_id,
            "handle": handle,
            "label": display_name,
        }
        if alias:
            result["alias"] = alias
        return result
    return None


def load_accounts_config(base_dir: Path) -> list[dict[str, str]]:
    config_path = base_dir / "accounts.json"
    if not config_path.exists():
        return []
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise DashboardError("accounts.json must contain a JSON array.")
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = normalize_account_spec(item)
        if normalized:
            result.append(normalized)
    if raw and not result:
        raise DashboardError("accounts.json does not contain any valid public account entries.")
    return result


def save_accounts_config(base_dir: Path, accounts: list[dict[str, str]]) -> None:
    (base_dir / "accounts.json").write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")


def add_account_spec(base_dir: Path, label: str = "", handle: str = "", alias: str = "") -> dict[str, str]:
    if not handle or not handle.strip():
        raise DashboardError("Public account handle is required.")
    normalized_handle = normalize_handle(handle)
    if not normalized_handle:
        raise DashboardError("Invalid account handle format. Please use @handle format.")
    accounts = load_accounts_config(base_dir)
    existing_handles = {item["handle"].lower() for item in accounts}
    if normalized_handle.lower() in existing_handles:
        raise DashboardError(f"Account {normalized_handle} already exists.")
    display_name = alias.strip() or label.strip() or normalized_handle
    base_id = sanitize_account_id(alias.strip() or normalized_handle)
    account_id = base_id
    suffix = 2
    existing_ids = {item["id"] for item in accounts}
    while account_id in existing_ids:
        account_id = f"{base_id}-{suffix}"
        suffix += 1
    new_account = {
        "id": account_id,
        "handle": normalized_handle,
        "label": display_name,
    }
    if alias.strip():
        new_account["alias"] = alias.strip()
    accounts.append(new_account)
    save_accounts_config(base_dir, accounts)
    return new_account


def remove_account_spec(base_dir: Path, account_id: str) -> bool:
    if not account_id or not account_id.strip():
        raise DashboardError("Account ID is required.")
    accounts = load_accounts_config(base_dir)
    filtered = [item for item in accounts if item["id"] != account_id]
    if len(filtered) == len(accounts):
        return False
    save_accounts_config(base_dir, filtered)
    return True


def find_account_spec(accounts: list[dict[str, str]], account_id: str) -> dict[str, str] | None:
    for account in accounts:
        if account.get("id") == account_id:
            return account
    return None


def cache_is_fresh(cached_at: float | int | None, ttl_seconds: int, now: float | int | None = None) -> bool:
    if cached_at is None:
        return False
    if now is None:
        now = time.time()
    return (float(now) - float(cached_at)) < ttl_seconds


def force_refresh_requested(path: str) -> bool:
    parsed = urlparse(path)
    params = parse_qs(parsed.query)
    return params.get("force", ["0"])[0] == "1"


def get_reporting_dates(now: datetime, tz: ZoneInfo) -> tuple[date, date]:
    local_today = now.astimezone(tz).date()
    return local_today, local_today - timedelta(days=1)


def pick_best_thumbnail_url(thumbnails: dict[str, Any]) -> str:
    for key in ("maxres", "standard", "high", "medium", "default"):
        thumb = thumbnails.get(key)
        if isinstance(thumb, dict) and thumb.get("url"):
            return str(thumb["url"])
    return ""


def filter_videos_published_on_day(videos: list[dict[str, Any]], target_day: date, tz: ZoneInfo = REPORTING_TIMEZONE) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for video in videos:
        published_at = str(video.get("publishedAt", ""))
        if not published_at:
            continue
        published_day = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(tz).date()
        if published_day == target_day:
            result.append(video)
    return result


def filter_videos_published_since(videos: list[dict[str, Any]], start_day: date, tz: ZoneInfo = REPORTING_TIMEZONE) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for video in videos:
        published_at = str(video.get("publishedAt", ""))
        if not published_at:
            continue
        published_day = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(tz).date()
        if published_day >= start_day:
            result.append(video)
    return result


def build_top_account_rows(accounts: list[dict[str, Any]], metric_key: str, total_key: str, limit: int = 5) -> list[dict[str, Any]]:
    ranked = sorted(accounts, key=lambda item: float(item.get(metric_key, 0) or 0), reverse=True)
    if limit and limit > 0:
        ranked = ranked[:limit]
    return [
        {
            "rank": index,
            "label": item.get("label", "Unknown"),
            "value": item.get(metric_key, 0),
            "total": item.get(total_key, 0),
            "accountId": item.get("id"),
        }
        for index, item in enumerate(ranked, start=1)
    ]


def build_top_video_rows(videos: list[dict[str, Any]], metric_key: str, limit: int = 5) -> list[dict[str, Any]]:
    ranked = sorted(videos, key=lambda item: float(item.get(metric_key, 0) or 0), reverse=True)
    if limit and limit > 0:
        ranked = ranked[:limit]
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(ranked, start=1):
        row = dict(item)
        row["rank"] = index
        rows.append(row)
    return rows


def build_public_gallery_rows(top_rows: list[dict[str, Any]], fallback_rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    source = top_rows if top_rows else fallback_rows
    if limit and limit > 0:
        source = source[:limit]
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(source, start=1):
        row = dict(item)
        row["rank"] = row.get("rank", index)
        if row.get("id") and not row.get("videoUrl"):
            row["videoUrl"] = f"https://www.youtube.com/watch?v={row['id']}"
        rows.append(row)
    return rows


def build_channel_profile_rows(channels: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for channel in channels[:limit]:
        rows.append(
            {
                "id": channel["id"],
                "label": channel.get("label", channel["id"]),
                "handle": channel.get("handle", ""),
                "channelTitle": channel.get("channel", {}).get("title", channel.get("label", "")),
                "thumbnail": channel.get("channel", {}).get("thumbnail", ""),
                "publishedAt": channel.get("channel", {}).get("publishedAt", ""),
                "country": channel.get("channel", {}).get("country", ""),
                "totalViews": int(channel.get("totalViews", 0)),
                "subscriberCount": int(channel.get("subscriberCount", 0)),
                "videoCount": int(channel.get("videoCount", 0)),
                "viewsDeltaYesterday": int(channel.get("viewsDelta", 0)),
                "hasBaseline": bool(channel.get("hasBaseline")),
            }
        )
    return rows


def snapshot_dir(base_dir: Path) -> Path:
    path = base_dir / SNAPSHOT_DIRNAME
    path.mkdir(exist_ok=True)
    return path


def snapshot_path(base_dir: Path, snapshot_date: date) -> Path:
    return snapshot_dir(base_dir) / f"{snapshot_date.isoformat()}.json"


def build_daily_snapshot(snapshot_date: date, channels: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "snapshotDate": snapshot_date.isoformat(),
        "channels": {
            channel["id"]: {
                "label": channel.get("label", channel["id"]),
                "totalViews": int(channel.get("totalViews", 0)),
                "subscriberCount": int(channel.get("subscriberCount", 0)),
                "videoCount": int(channel.get("videoCount", 0)),
            }
            for channel in channels
        },
    }


def save_daily_snapshot(base_dir: Path, payload: dict[str, Any]) -> Path:
    snapshot_date = date.fromisoformat(payload["snapshotDate"])
    path = snapshot_path(base_dir, snapshot_date)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_daily_snapshot(base_dir: Path, snapshot_date: date) -> dict[str, Any]:
    path = snapshot_path(base_dir, snapshot_date)
    if not path.exists():
        return {"snapshotDate": snapshot_date.isoformat(), "channels": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def compute_channel_delta(current: dict[str, Any], previous_snapshot: dict[str, Any]) -> dict[str, int]:
    previous = previous_snapshot.get("channels", {}).get(current["id"], {})
    has_baseline = bool(previous)
    if not has_baseline:
        return {
            "viewsDelta": 0,
            "subscriberDelta": 0,
            "videoDelta": 0,
            "hasBaseline": False,
        }
    return {
        "viewsDelta": max(0, int(current.get("totalViews", 0)) - int(previous.get("totalViews", 0))),
        "subscriberDelta": max(0, int(current.get("subscriberCount", 0)) - int(previous.get("subscriberCount", 0))),
        "videoDelta": max(0, int(current.get("videoCount", 0)) - int(previous.get("videoCount", 0))),
        "hasBaseline": True,
    }


class YouTubeDashboardService:
    def __init__(self, base_dir: Path, api_key: str | None, account_specs: list[dict[str, str]] | None = None) -> None:
        self.base_dir = base_dir
        self.api_key = api_key
        self.account_specs = account_specs or []
        self.cache_ttl_seconds = CACHE_TTL_SECONDS
        self.enable_extended_video_boards = False
        self._cached_payload: dict[str, Any] | None = None
        self._cached_at: float | None = None

    def clear_cache(self) -> None:
        self._cached_payload = None
        self._cached_at = None

    def perform_json_request(self, request: Request, error_prefix: str) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise DashboardError(f"{error_prefix}: HTTP {exc.code} {details}") from exc
        except URLError as exc:
            raise DashboardError(f"{error_prefix}: {exc.reason}") from exc

    def public_api_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise DashboardError("YT_API_KEY is required for public-data mode.")
        query = urlencode({**params, "key": self.api_key})
        request = Request(f"{YOUTUBE_DATA_BASE}{path}?{query}", method="GET")
        return self.perform_json_request(request, "Public API request failed")

    def resolve_channel_id_from_handle(self, handle: str) -> str:
        response = self.public_api_get("/channels", {"part": "id", "forHandle": handle.lstrip("@")})
        items = response.get("items", [])
        if not items:
            raise DashboardError(f"Public channel not found for handle {handle}.")
        return str(items[0]["id"])

    def fetch_channel_profile(self, channel_id: str) -> dict[str, Any]:
        response = self.public_api_get("/channels", {"part": "snippet,statistics,contentDetails", "id": channel_id})
        items = response.get("items", [])
        if not items:
            raise DashboardError(f"Channel {channel_id} is not publicly available.")
        channel = items[0]
        snippet = channel.get("snippet", {})
        statistics = channel.get("statistics", {})
        uploads = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        return {
            "id": channel.get("id"),
            "title": snippet.get("title", channel_id),
            "handle": snippet.get("customUrl", ""),
            "thumbnail": pick_best_thumbnail_url(snippet.get("thumbnails", {})),
            "publishedAt": snippet.get("publishedAt", ""),
            "country": snippet.get("country", ""),
            "totalViews": int(statistics.get("viewCount", 0)),
            "subscriberCount": int(statistics.get("subscriberCount", 0)),
            "videoCount": int(statistics.get("videoCount", 0)),
            "uploadsPlaylistId": uploads,
        }

    def fetch_recent_uploads(self, uploads_playlist_id: str) -> list[dict[str, Any]]:
        if not uploads_playlist_id:
            return []
        playlist_response = self.public_api_get(
            "/playlistItems",
            {"part": "snippet,contentDetails", "playlistId": uploads_playlist_id, "maxResults": 20},
        )
        video_ids = [
            item.get("contentDetails", {}).get("videoId")
            for item in playlist_response.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]
        if not video_ids:
            return []
        videos_response = self.public_api_get("/videos", {"part": "snippet,statistics", "id": ",".join(video_ids)})
        result: list[dict[str, Any]] = []
        for item in videos_response.get("items", []):
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            result.append(
                {
                    "id": item.get("id"),
                    "title": snippet.get("title", "Untitled"),
                    "publishedAt": snippet.get("publishedAt"),
                    "thumbnail": pick_best_thumbnail_url(snippet.get("thumbnails", {})),
                    "currentViewCount": int(statistics.get("viewCount", 0)),
                    "likeCount": int(statistics.get("likeCount", 0)),
                    "commentCount": int(statistics.get("commentCount", 0)),
                    "videoUrl": f"https://www.youtube.com/watch?v={item.get('id')}",
                }
            )
        return result

    def fetch_account_snapshot(self, spec: dict[str, str], today: date, yesterday: date) -> dict[str, Any]:
        channel_id = self.resolve_channel_id_from_handle(spec["handle"])
        profile = self.fetch_channel_profile(channel_id)
        uploads = self.fetch_recent_uploads(profile["uploadsPlaylistId"])
        published_yesterday = build_top_video_rows(
            [
                {**video, "accountLabel": spec["label"], "accountId": spec["id"]}
                for video in filter_videos_published_on_day(uploads, yesterday)
            ],
            "currentViewCount",
        )
        published_today = build_top_video_rows(
            [
                {**video, "accountLabel": spec["label"], "accountId": spec["id"]}
                for video in filter_videos_published_on_day(uploads, today)
            ],
            "currentViewCount",
        )
        recent_28_day_videos = build_top_video_rows(
            [
                {**video, "accountLabel": spec["label"], "accountId": spec["id"]}
                for video in filter_videos_published_since(uploads, today - timedelta(days=27))
            ],
            "currentViewCount",
            limit=0,
        )
        return {
            "id": spec["id"],
            "label": spec["label"],
            "alias": spec.get("alias", ""),
            "handle": spec["handle"],
            "channel": {
                "id": profile["id"],
                "title": profile["title"],
                "handle": profile["handle"] or spec["handle"],
                "thumbnail": profile["thumbnail"],
                "publishedAt": profile["publishedAt"],
                "country": profile["country"],
            },
            "totalViews": profile["totalViews"],
            "subscriberCount": profile["subscriberCount"],
            "videoCount": profile["videoCount"],
            "recentUploads": uploads,
            "recent28DayVideos": recent_28_day_videos,
            "publishedYesterday": published_yesterday,
            "publishedToday": published_today,
            "latestVideos": build_top_video_rows(
                [{**video, "accountLabel": spec["label"], "accountId": spec["id"]} for video in uploads],
                "currentViewCount",
            )[:5],
        }

    def get_dashboard_payload(self) -> dict[str, Any]:
        if cache_is_fresh(self._cached_at, self.cache_ttl_seconds):
            return dict(self._cached_payload or {})

        now = datetime.now(timezone.utc)
        today, yesterday = get_reporting_dates(now, REPORTING_TIMEZONE)
        if not self.account_specs:
            payload = {
                "generatedAt": now.astimezone(REPORTING_TIMEZONE).isoformat(),
                "cache": {"ttlSeconds": self.cache_ttl_seconds, "cachedAt": now.astimezone(REPORTING_TIMEZONE).isoformat()},
                "accounts": [],
                "summary": {
                    "accountCount": 0,
                    "configuredAccountCount": 0,
                    "dateLabels": {"today": today.isoformat(), "yesterday": yesterday.isoformat()},
                },
                "cards": {},
            }
            self._cached_payload = payload
            self._cached_at = time.time()
            return payload

        previous_snapshot = load_daily_snapshot(self.base_dir, yesterday)
        snapshots: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for spec in self.account_specs:
            try:
                snapshot = self.fetch_account_snapshot(spec, today, yesterday)
                delta = compute_channel_delta(snapshot, previous_snapshot)
                snapshot.update(delta)
                snapshots.append(snapshot)
            except DashboardError as exc:
                errors.append(
                    {
                        "id": spec["id"],
                        "label": spec["label"],
                        "handle": spec["handle"],
                        "status": "error",
                        "statusReason": str(exc),
                    }
                )

        if snapshots:
            save_daily_snapshot(self.base_dir, build_daily_snapshot(today, snapshots))

        playback_rows = build_top_account_rows(
            [snapshot for snapshot in snapshots if snapshot.get("hasBaseline")],
            "viewsDelta",
            "totalViews",
            limit=0,
        )
        subscriber_rows = build_top_account_rows(snapshots, "subscriberCount", "subscriberCount", limit=0)
        video_count_rows = build_top_account_rows(snapshots, "videoCount", "videoCount", limit=0)
        published_yesterday_rows = build_top_video_rows(
            [video for snapshot in snapshots for video in snapshot["publishedYesterday"]],
            "currentViewCount",
            limit=0,
        )
        latest_video_rows: list[dict[str, Any]] = []
        latest_liked_rows: list[dict[str, Any]] = []
        latest_commented_rows: list[dict[str, Any]] = []
        if self.enable_extended_video_boards:
            latest_video_rows = build_top_video_rows(
                [video for snapshot in snapshots for video in snapshot["recent28DayVideos"]],
                "currentViewCount",
                limit=0,
            )
            latest_liked_rows = build_top_video_rows(
                [video for snapshot in snapshots for video in snapshot["recent28DayVideos"]],
                "likeCount",
                limit=0,
            )
            latest_commented_rows = build_top_video_rows(
                [video for snapshot in snapshots for video in snapshot["recent28DayVideos"]],
                "commentCount",
                limit=0,
            )
        today_rows = build_top_video_rows(
            [video for snapshot in snapshots for video in snapshot["publishedToday"]],
            "currentViewCount",
            limit=0,
        )
        gallery_rows = build_public_gallery_rows(published_yesterday_rows, published_yesterday_rows, limit=0)
        channel_profile_rows = build_channel_profile_rows(snapshots)

        payload = {
            "generatedAt": now.astimezone(REPORTING_TIMEZONE).isoformat(),
            "cache": {"ttlSeconds": self.cache_ttl_seconds, "cachedAt": now.astimezone(REPORTING_TIMEZONE).isoformat()},
            "accounts": [
                {
                    "id": snapshot["id"],
                    "label": snapshot["label"],
                    "handle": snapshot["handle"],
                    "status": "ready",
                    "hasBaseline": bool(snapshot.get("hasBaseline")),
                    "totalViews": snapshot["totalViews"],
                    "subscriberCount": snapshot["subscriberCount"],
                    "videoCount": snapshot["videoCount"],
                    "viewsDeltaYesterday": snapshot["viewsDelta"],
                }
                for snapshot in snapshots
            ] + errors,
            "summary": {
                "accountCount": len(snapshots),
                "configuredAccountCount": len(self.account_specs),
                "dateLabels": {"today": today.isoformat(), "yesterday": yesterday.isoformat()},
                "totals": {
                    "views": sum(snapshot["totalViews"] for snapshot in snapshots),
                    "subscribers": sum(snapshot["subscriberCount"] for snapshot in snapshots),
                    "videos": sum(snapshot["videoCount"] for snapshot in snapshots),
                },
            },
            "cards": {
                "playbackDelta": {
                    "headlineTotalViews": sum(snapshot["totalViews"] for snapshot in snapshots),
                    "headlineDeltaViews": sum(snapshot["viewsDelta"] for snapshot in snapshots),
                    "rows": playback_rows,
                },
                "subscriberBoard": {
                    "headlineTotalSubscribers": sum(snapshot["subscriberCount"] for snapshot in snapshots),
                    "rows": subscriber_rows,
                },
                "videoCountBoard": {
                    "headlineTotalVideos": sum(snapshot["videoCount"] for snapshot in snapshots),
                    "rows": video_count_rows,
                },
                "publishedYesterday": {
                    "count": len(published_yesterday_rows),
                    "rows": published_yesterday_rows,
                },
                "todayPublished": {
                    "count": len(today_rows),
                    "rows": today_rows,
                },
                "latestVideos": {
                    "rows": latest_video_rows,
                },
                "likedVideos": {
                    "rows": latest_liked_rows,
                },
                "commentedVideos": {
                    "rows": latest_commented_rows,
                },
                "topVideosYesterday": {
                    "rows": gallery_rows,
                },
                "channelProfiles": {
                    "rows": channel_profile_rows,
                },
            },
        }
        self._cached_payload = payload
        self._cached_at = time.time()
        return payload


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    _last_account_operation: float = 0
    _min_operation_interval: float = 2.0

    def __init__(self, *args: Any, directory: str, service: YouTubeDashboardService, **kwargs: Any) -> None:
        self.service = service
        super().__init__(*args, directory=directory, **kwargs)

    def check_rate_limit(self) -> bool:
        now = time.time()
        if now - self._last_account_operation < self._min_operation_interval:
            return False
        self._last_account_operation = now
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/dashboard":
            self.handle_dashboard_api(force_refresh_requested(self.path))
            return
        if parsed.path == "/api/accounts":
            self.handle_accounts_list()
            return
        if parsed.path == "/":
            self.path = "/dashboard-prototype.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/accounts":
            self.handle_accounts_create()
            return
        self.send_error(404)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/accounts/"):
            account_id = unquote(parsed.path.split("/")[-1])
            self.handle_accounts_delete(account_id)
            return
        self.send_error(404)

    def handle_dashboard_api(self, force_refresh: bool = False) -> None:
        try:
            if force_refresh:
                self.service.clear_cache()
            body = json.dumps(self.service.get_dashboard_payload(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        except DashboardError as exc:
            body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def handle_accounts_list(self) -> None:
        body = json.dumps({"accounts": self.service.account_specs}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_accounts_create(self) -> None:
        if not self.check_rate_limit():
            body = json.dumps({"error": "操作过于频繁，请稍后再试。"}, ensure_ascii=False).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            payload = self.read_json_body()
            handle = str(payload.get("handle", "")).strip()
            alias = str(payload.get("alias", "")).strip()
            label = str(payload.get("label", "")).strip()
            new_account = add_account_spec(self.service.base_dir, label=label, handle=handle, alias=alias)
            self.service.account_specs = load_accounts_config(self.service.base_dir)
            self.service.clear_cache()
            body = json.dumps({"account": new_account}, ensure_ascii=False).encode("utf-8")
            self.send_response(201)
        except (DashboardError, json.JSONDecodeError) as exc:
            body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(400)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_accounts_delete(self, account_id: str) -> None:
        if not self.check_rate_limit():
            body = json.dumps({"error": "操作过于频繁，请稍后再试。"}, ensure_ascii=False).encode("utf-8")
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            removed = remove_account_spec(self.service.base_dir, account_id)
            if not removed:
                raise DashboardError("Account not found.")
            self.service.account_specs = load_accounts_config(self.service.base_dir)
            self.service.clear_cache()
            body = json.dumps({"removed": True, "accountId": account_id}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        except DashboardError as exc:
            body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(404)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def build_handler(directory: Path, service: YouTubeDashboardService):
    def handler(*args: Any, **kwargs: Any) -> DashboardRequestHandler:
        return DashboardRequestHandler(*args, directory=str(directory), service=service, **kwargs)

    return handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local YouTube public dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Server host, default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8130, help="Server port, default: 8130")
    parser.add_argument("--api-key", default=None, help="YouTube Data API key.")
    parser.add_argument("--cache-ttl", type=int, default=CACHE_TTL_SECONDS, help="Server-side cache TTL in seconds.")
    parser.add_argument("--extended-video-boards", action="store_true", help="Enable extra 28-day video boards (07/08/09).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    env_values = resolve_env_values(base_dir)
    api_key = args.api_key or env_values.get("YT_API_KEY") or os.getenv("YT_API_KEY")
    account_specs = load_accounts_config(base_dir)
    service = YouTubeDashboardService(base_dir=base_dir, api_key=api_key, account_specs=account_specs)
    service.cache_ttl_seconds = args.cache_ttl
    service.enable_extended_video_boards = bool(args.extended_video_boards)
    server = ThreadingHTTPServer((args.host, args.port), build_handler(base_dir, service))
    print(f"Dashboard available at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
