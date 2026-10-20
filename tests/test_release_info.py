"""Catalog discovery is independent of preferences and keeps safe offline information."""

import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from test_library import APP
from test_prereleases import metadata, release_list_html
from test_releases import Response, public_assets

from appharbour import releases
from appharbour.release_info import cached_info, discover_releases, info_path, remember_release


class ReleaseInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.stable = {**metadata("1.2.3"), "is_latest": True}
        self.beta = metadata("1.3.0-beta.10", True)
        self.entries = [self.beta, self.stable]

    def discover(self):
        with patch.object(releases, "api_entries", return_value=self.entries):
            return discover_releases(APP, Event(), self.cache, force=True)

    def test_both_channels_use_two_api_requests_and_keep_user_preference(self):
        with patch.object(
            releases,
            "read_metadata",
            side_effect=[json.dumps(self.stable).encode(), json.dumps(self.entries).encode()],
        ) as read:
            info = discover_releases(APP, Event(), self.cache)
        self.assertEqual(read.call_count, 2)
        self.assertIn("per_page=30", read.call_args.args[0])
        self.assertEqual(info.choice(False).version, "1.2.3")
        self.assertEqual(info.choice(True).version, "1.3.0-beta.10")
        self.assertFalse(APP.include_prereleases)
        self.assertTrue(info.stable.release.is_latest)
        self.assertFalse(info.preview.release.is_latest)
        self.assertEqual(cached_info(APP, self.cache), info)

    def test_prefixed_short_versions_keep_channel_ordering_and_cache(self):
        self.stable = {**metadata("0.6"), "is_latest": True}
        self.beta = metadata("0.7-beta.10", True)
        for data in (self.stable, self.beta):
            old_tag = data["tag_name"]
            data["tag_name"] = "studio-" + old_tag
            for asset in data["assets"]:
                asset["browser_download_url"] = asset["browser_download_url"].replace(
                    old_tag, data["tag_name"]
                )
        self.entries = [self.beta, self.stable, metadata("0.7-beta.2", True)]
        info = self.discover()
        self.assertEqual(info.choice(False).version, "0.6")
        self.assertEqual(info.choice(True).version, "0.7-beta.10")
        self.assertEqual(cached_info(APP, self.cache), info)
        self.assertEqual(releases.cached_release(APP, self.cache), info.stable.release)

    def test_unsupported_tags_keep_the_actionable_discovery_error(self):
        self.entries = [{**self.stable, "tag_name": "nightly"}]
        with self.assertRaisesRegex(ValueError, "AppImage.*version tag"):
            self.discover()

    def test_stable_replaces_preview_and_designated_latest_is_preserved(self):
        self.entries.append({**metadata("1.4.0"), "is_latest": False})
        info = self.discover()
        self.assertEqual(info.choice(False).version, "1.2.3")
        self.assertEqual(info.choice(True).version, "1.4.0")
        self.assertFalse(info.choice(True).release.is_latest)
        self.assertEqual(cached_info(APP, self.cache), info)
        self.stable = {**metadata("1.3.0"), "is_latest": True}
        self.entries = [self.stable, self.beta]
        info = self.discover()
        self.assertEqual(info.choice(True).version, "1.3.0")
        self.assertFalse(info.choice(True).release.prerelease)

    def test_only_preview_needs_opt_in_and_obsolete_preview_is_not_selected(self):
        self.entries = [self.beta]
        info = self.discover()
        self.assertIsNone(info.choice(False))
        self.assertIn("Only prereleases", info.error(False))
        self.assertTrue(info.choice(True).release.prerelease)
        self.entries = [self.stable, metadata("1.2.0-beta.1", True)]
        self.assertEqual(self.discover().choice(True).version, "1.2.3")

    def test_unusable_new_preview_is_explained_without_falling_back_when_opted_in(self):
        self.beta["assets"] = []
        info = self.discover()
        self.assertIsNotNone(info.choice(False).release)
        self.assertIsNone(info.choice(True).release)
        self.assertTrue(info.error(True))
        self.assertIsNone(
            releases.cached_release(replace(APP, include_prereleases=True), self.cache)
        )
        self.assertEqual(cached_info(APP, self.cache), info)

    def test_offline_refresh_keeps_stale_cache_and_download_cache_expires(self):
        info = self.discover()
        path = info_path(APP, self.cache)
        data = json.loads(path.read_text())
        data["checked_at"] = time.time() - 3600
        path.write_text(json.dumps(data))
        contents = path.read_bytes()
        stale = cached_info(APP, self.cache)
        self.assertIsNotNone(stale)
        self.assertFalse(stale.fresh())
        with patch.object(releases, "api_entries", side_effect=OSError("Offline")):
            with self.assertRaisesRegex(OSError, "Offline"):
                discover_releases(APP, Event(), self.cache)
        self.assertEqual(path.read_bytes(), contents)
        for include in (False, True):
            channel = replace(APP, include_prereleases=include)
            item_path = releases.release_cache_path(channel, self.cache)
            item = json.loads(item_path.read_text())
            item["checked_at"] = data["checked_at"]
            item_path.write_text(json.dumps(item))
            self.assertIsNone(releases.cached_release(channel, self.cache))
        self.assertEqual(cached_info(APP, self.cache).stable, info.stable)

    def test_fresh_cache_avoids_network_and_manual_refresh_bypasses_it(self):
        info = self.discover()
        with patch.object(releases, "api_entries", return_value=self.entries) as read:
            self.assertEqual(discover_releases(APP, Event(), self.cache), info)
            read.assert_not_called()
            discover_releases(APP, Event(), self.cache, force=True)
            read.assert_called_once()

    def test_corrupt_or_foreign_cached_information_is_rejected(self):
        self.discover()
        path = info_path(APP, self.cache)
        original = json.loads(path.read_text())
        for value in (None, True, float("nan"), 1e100):
            path.write_text(json.dumps({**original, "checked_at": value}))
            self.assertIsNone(cached_info(APP, self.cache))
        for channel in ("stable", "preview"):
            data = json.loads(json.dumps(original))
            data[channel]["release"]["url"] = (
                "https://github.com/other/app/releases/download/v1/a.AppImage"
            )
            path.write_text(json.dumps(data))
            self.assertIsNone(cached_info(APP, self.cache))
        path.write_text("[]")
        self.assertIsNone(cached_info(APP, self.cache))

    def test_quota_fallback_discovers_both_channels_with_the_same_checksums(self):
        info = self.discover()
        with (
            patch.object(
                releases, "api_entries", side_effect=releases.RateLimited("api.github.com", 0)
            ),
            patch.object(
                releases,
                "request",
                side_effect=[
                    Response(APP.source_url + "/releases/tag/v1.2.3"),
                    Response(
                        info.stable.release.url, {"Content-Length": str(info.stable.release.size)}
                    ),
                    Response(
                        info.preview.release.url, {"Content-Length": str(info.preview.release.size)}
                    ),
                ],
            ),
            patch.object(
                releases,
                "read_metadata",
                side_effect=[
                    release_list_html(self.entries, "v1.2.3"),
                    public_assets(info.stable.release),
                    public_assets(info.preview.release),
                ],
            ),
        ):
            found = discover_releases(APP, Event(), self.cache, force=True)
        self.assertEqual(found.stable, info.stable)
        self.assertEqual(found.preview, info.preview)

    def test_new_download_information_updates_one_channel_without_faking_freshness(self):
        info = self.discover()
        release = replace(
            info.stable.release,
            version="1.3.0",
            name=info.stable.release.name.replace("1.2.3", "1.3.0"),
            url=info.stable.release.url.replace("1.2.3", "1.3.0"),
        )
        updated = remember_release(APP, release, self.cache)
        self.assertEqual(updated.stable.release, release)
        self.assertEqual(updated.preview, info.preview)
        self.assertEqual(updated.checked_at, info.checked_at)
        self.assertEqual(cached_info(APP, self.cache), updated)
