import json
import shutil
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from youtube_dashboard_app import (
    DashboardError,
    YouTubeDashboardService,
    add_account_spec,
    build_daily_snapshot,
    build_public_gallery_rows,
    build_top_account_rows,
    build_top_video_rows,
    build_channel_profile_rows,
    filter_videos_published_since,
    compute_channel_delta,
    load_accounts_config,
    normalize_account_spec,
    save_daily_snapshot,
)


class PublicDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_root = Path("test_public_tmp")
        self.test_root.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)

    def test_normalize_account_spec_supports_handle_and_alias(self) -> None:
        account = normalize_account_spec(
            {
                "id": "employee-1",
                "handle": "@GolemAnton",
                "alias": "员工1",
            }
        )
        self.assertEqual(account["handle"], "@GolemAnton")
        self.assertEqual(account["alias"], "员工1")
        self.assertEqual(account["label"], "员工1")

    def test_load_accounts_config_reads_public_accounts_without_token_files(self) -> None:
        (self.test_root / "accounts.json").write_text(
            json.dumps(
                [
                    {"id": "employee-1", "handle": "@GolemAnton", "alias": "员工1"},
                    {"id": "employee-2", "handle": "@CrotstOqu", "alias": "员工2"},
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        accounts = load_accounts_config(self.test_root)
        self.assertEqual(accounts[0]["label"], "员工1")
        self.assertEqual(accounts[1]["handle"], "@CrotstOqu")

    def test_add_account_spec_creates_public_account(self) -> None:
        account = add_account_spec(self.test_root, handle="@GolemAnton", alias="员工1")
        self.assertEqual(account["handle"], "@GolemAnton")
        self.assertEqual(account["alias"], "员工1")
        self.assertNotIn("token_file", account)

    def test_compute_channel_delta_uses_previous_snapshot(self) -> None:
        previous = {"channels": {"employee-1": {"totalViews": 1000, "subscriberCount": 50, "videoCount": 10}}}
        current = {"id": "employee-1", "totalViews": 1350, "subscriberCount": 57, "videoCount": 12}
        delta = compute_channel_delta(current, previous)
        self.assertEqual(delta["viewsDelta"], 350)
        self.assertEqual(delta["subscriberDelta"], 7)
        self.assertEqual(delta["videoDelta"], 2)
        self.assertTrue(delta["hasBaseline"])

    def test_compute_channel_delta_clamps_negative_deltas_to_zero(self) -> None:
        previous = {"channels": {"employee-1": {"totalViews": 2000, "subscriberCount": 50, "videoCount": 10}}}
        current = {"id": "employee-1", "totalViews": 1800, "subscriberCount": 48, "videoCount": 9}
        delta = compute_channel_delta(current, previous)
        self.assertEqual(delta["viewsDelta"], 0)
        self.assertEqual(delta["subscriberDelta"], 0)
        self.assertEqual(delta["videoDelta"], 0)
        self.assertTrue(delta["hasBaseline"])

    def test_compute_channel_delta_without_snapshot_marks_no_baseline(self) -> None:
        previous = {"channels": {}}
        current = {"id": "employee-1", "totalViews": 1350, "subscriberCount": 57, "videoCount": 12}
        delta = compute_channel_delta(current, previous)
        self.assertEqual(delta["viewsDelta"], 0)
        self.assertEqual(delta["subscriberDelta"], 0)
        self.assertEqual(delta["videoDelta"], 0)
        self.assertFalse(delta["hasBaseline"])

    def test_snapshot_roundtrip_persists_relative_daily_file(self) -> None:
        payload = build_daily_snapshot(
            snapshot_date=date(2026, 4, 2),
            channels=[{"id": "employee-1", "label": "员工1", "totalViews": 99, "subscriberCount": 10, "videoCount": 2}],
        )
        path = save_daily_snapshot(self.test_root, payload)
        self.assertTrue(path.exists())
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["channels"]["employee-1"]["totalViews"], 99)

    def test_build_public_gallery_rows_uses_top_rows_then_fallback(self) -> None:
        top_rows = [{"id": "abc", "title": "Top", "thumbnail": "top.jpg", "accountLabel": "员工1", "currentViewCount": 0}]
        fallback_rows = [{"id": "def", "title": "Fallback", "thumbnail": "f.jpg", "accountLabel": "员工2", "currentViewCount": 0}]
        self.assertEqual(build_public_gallery_rows(top_rows, fallback_rows)[0]["id"], "abc")
        self.assertEqual(build_public_gallery_rows([], fallback_rows)[0]["id"], "def")
        self.assertTrue(build_public_gallery_rows([], fallback_rows)[0]["videoUrl"].startswith("https://www.youtube.com/watch?v="))
        self.assertEqual(build_public_gallery_rows(top_rows, fallback_rows, limit=0)[0]["id"], "abc")
        self.assertIn("currentViewCount", build_public_gallery_rows(top_rows, fallback_rows, limit=0)[0])

    def test_filter_videos_published_since_keeps_recent_28_day_videos(self) -> None:
        videos = [
            {"id": "old", "publishedAt": "2026-03-01T00:00:00Z"},
            {"id": "recent-a", "publishedAt": "2026-03-20T00:00:00Z"},
            {"id": "recent-b", "publishedAt": "2026-04-08T00:00:00Z"},
        ]
        rows = filter_videos_published_since(
            videos,
            start_day=date(2026, 3, 12),
        )
        self.assertEqual([item["id"] for item in rows], ["recent-a", "recent-b"])

    def test_build_top_account_rows_still_sorts_for_public_metrics(self) -> None:
        rows = build_top_account_rows(
            [
                {"label": "员工1", "viewsDeltaYesterday": 100, "totalViews": 1000},
                {"label": "员工2", "viewsDeltaYesterday": 300, "totalViews": 2000},
            ],
            metric_key="viewsDeltaYesterday",
            total_key="totalViews",
        )
        self.assertEqual(rows[0]["label"], "员工2")
        self.assertEqual(len(rows), 2)

    def test_build_top_video_rows_sorts_current_public_views(self) -> None:
        rows = build_top_video_rows(
            [
                {"title": "a", "currentViewCount": 5},
                {"title": "b", "currentViewCount": 20},
            ],
            metric_key="currentViewCount",
        )
        self.assertEqual(rows[0]["title"], "b")

    def test_build_top_account_rows_returns_all_rows_when_limit_not_set(self) -> None:
        rows = build_top_account_rows(
            [
                {"label": "A", "viewsDeltaYesterday": 1, "totalViews": 1},
                {"label": "B", "viewsDeltaYesterday": 2, "totalViews": 2},
                {"label": "C", "viewsDeltaYesterday": 3, "totalViews": 3},
                {"label": "D", "viewsDeltaYesterday": 4, "totalViews": 4},
                {"label": "E", "viewsDeltaYesterday": 5, "totalViews": 5},
                {"label": "F", "viewsDeltaYesterday": 6, "totalViews": 6},
            ],
            metric_key="viewsDeltaYesterday",
            total_key="totalViews",
            limit=0,
        )
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["label"], "F")

    def test_build_top_video_rows_returns_all_rows_when_limit_not_set(self) -> None:
        rows = build_top_video_rows(
            [
                {"title": "a", "currentViewCount": 1},
                {"title": "b", "currentViewCount": 2},
                {"title": "c", "currentViewCount": 3},
                {"title": "d", "currentViewCount": 4},
                {"title": "e", "currentViewCount": 5},
                {"title": "f", "currentViewCount": 6},
            ],
            metric_key="currentViewCount",
            limit=0,
        )
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["title"], "f")

    def test_build_channel_profile_rows_keeps_alias_and_public_totals(self) -> None:
        rows = build_channel_profile_rows(
            [
                {
                    "id": "employee-1",
                    "label": "员工1",
                    "handle": "@GolemAnton",
                    "channel": {
                        "title": "Anton Channel",
                        "thumbnail": "thumb.jpg",
                        "publishedAt": "2024-01-01T00:00:00Z",
                    },
                    "totalViews": 1000,
                    "subscriberCount": 50,
                    "videoCount": 10,
                    "viewsDelta": 100,
                }
            ]
        )
        self.assertEqual(rows[0]["label"], "员工1")
        self.assertEqual(rows[0]["channelTitle"], "Anton Channel")
        self.assertEqual(rows[0]["subscriberCount"], 50)

    def test_invalid_public_accounts_config_raises(self) -> None:
        (self.test_root / "accounts.json").write_text('[{"id":"x"}]', encoding="utf-8")
        with self.assertRaises(DashboardError):
            load_accounts_config(self.test_root)

    def test_service_can_disable_extended_video_boards(self) -> None:
        class FakeService(YouTubeDashboardService):
            def fetch_account_snapshot(self, spec, today, yesterday):
                return {
                    "id": spec["id"],
                    "label": spec["label"],
                    "handle": spec["handle"],
                    "channel": {
                        "title": spec["label"],
                        "thumbnail": "",
                        "publishedAt": "",
                        "country": "",
                    },
                    "totalViews": 100,
                    "subscriberCount": 10,
                    "videoCount": 2,
                    "viewsDelta": 5,
                    "publishedYesterday": [],
                    "publishedToday": [],
                    "recent28DayVideos": [
                        {
                            "id": "v1",
                            "title": "Video 1",
                            "currentViewCount": 100,
                            "likeCount": 10,
                            "commentCount": 1,
                            "videoUrl": "https://www.youtube.com/watch?v=v1",
                            "thumbnail": "",
                            "accountLabel": spec["label"],
                            "accountId": spec["id"],
                        }
                    ],
                    "latestVideos": [],
                }

        service = FakeService(
            base_dir=self.test_root,
            api_key="fake",
            account_specs=[{"id": "a", "handle": "@demo", "label": "Demo"}],
        )
        service.enable_extended_video_boards = False
        payload = service.get_dashboard_payload()
        self.assertEqual(payload["cards"]["latestVideos"]["rows"], [])
        self.assertEqual(payload["cards"]["likedVideos"]["rows"], [])
        self.assertEqual(payload["cards"]["commentedVideos"]["rows"], [])

    def test_service_excludes_accounts_without_baseline_from_playback_ranking(self) -> None:
        class FakeService(YouTubeDashboardService):
            def fetch_account_snapshot(self, spec, today, yesterday):
                totals = {"with-baseline": 1200, "new-account": 800}
                return {
                    "id": spec["id"],
                    "label": spec["label"],
                    "handle": spec["handle"],
                    "channel": {"title": spec["label"], "thumbnail": "", "publishedAt": "", "country": ""},
                    "totalViews": totals[spec["id"]],
                    "subscriberCount": 10,
                    "videoCount": 2,
                    "publishedYesterday": [],
                    "publishedToday": [],
                    "recent28DayVideos": [],
                    "latestVideos": [],
                }

        save_daily_snapshot(
            self.test_root,
            {
                "snapshotDate": "2026-04-08",
                "channels": {
                    "with-baseline": {
                        "label": "老账号",
                        "totalViews": 1000,
                        "subscriberCount": 8,
                        "videoCount": 2,
                    }
                },
            },
        )
        service = FakeService(
            base_dir=self.test_root,
            api_key="fake",
            account_specs=[
                {"id": "with-baseline", "handle": "@old", "label": "老账号"},
                {"id": "new-account", "handle": "@new", "label": "新账号"},
            ],
        )

        original_get_reporting_dates = __import__("youtube_dashboard_app").get_reporting_dates
        import youtube_dashboard_app as mod
        mod.get_reporting_dates = lambda now, tz: (date(2026, 4, 9), date(2026, 4, 8))
        try:
            payload = service.get_dashboard_payload()
        finally:
            mod.get_reporting_dates = original_get_reporting_dates

        self.assertEqual(len(payload["cards"]["playbackDelta"]["rows"]), 1)
        self.assertEqual(payload["cards"]["playbackDelta"]["rows"][0]["accountId"], "with-baseline")
        new_account = next(item for item in payload["accounts"] if item["id"] == "new-account")
        self.assertFalse(new_account["hasBaseline"])


if __name__ == "__main__":
    unittest.main()
