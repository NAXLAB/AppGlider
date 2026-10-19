"""Rate-limit recovery keeps release checks bounded and downloads verified."""

import io
import json
import tempfile
import time
import unittest
from dataclasses import asdict, replace
from email.message import Message
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError

from test_library import APP, appimage_bytes, release_for

from appharbour import releases
from appharbour.storage import atomic_json


class Response(io.BytesIO):
    def __init__(self, url, headers=None):
        super().__init__()
        self.url = url
        self.headers = headers or {}

    def geturl(self):
        return self.url


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.release = release_for("1.2.3", appimage_bytes())
        self.cache_file = self.cache / f"{APP.id}-{releases.architecture()}.json"
        cooldown = patch.dict(releases._RETRY_AFTER, {}, clear=True)
        cooldown.start()
        self.addCleanup(cooldown.stop)

    def test_limit_headers_trigger_cooldown_without_repeated_requests(self):
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = "1600"
        error = HTTPError("https://api.github.com/", 403, "Forbidden", headers, io.BytesIO())
        with (
            patch.object(releases.time, "time", return_value=1000),
            patch.object(releases, "build_opener") as opener,
        ):
            opener.return_value.open.side_effect = error
            for _ in range(2):
                with self.assertRaises(releases.RateLimited) as raised:
                    releases.request("https://api.github.com/")
                self.assertEqual(raised.exception.retry_at, 1600)
            opener.return_value.open.assert_called_once()
            opener.return_value.open.side_effect = None
            with patch.object(releases.time, "time", return_value=1601):
                releases.request("https://api.github.com/")
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_retry_after_and_body_only_rate_limits(self):
        for code, body, expected in ((429, b"", 1300), (403, b"API rate limit exceeded", 1300)):
            headers = Message()
            headers["Retry-After"] = "300"
            if code == 403:
                del headers["Retry-After"]
                expected = 1060
            error = HTTPError("https://api.github.com/", code, "Limited", headers, io.BytesIO(body))
            with (
                self.subTest(code=code),
                patch.dict(releases._RETRY_AFTER, {}, clear=True),
                patch.object(releases.time, "time", return_value=1000),
                patch.object(releases, "build_opener") as opener,
            ):
                opener.return_value.open.side_effect = error
                with self.assertRaises(releases.RateLimited) as raised:
                    releases.request("https://api.github.com/")
                self.assertEqual(raised.exception.retry_at, expected)

    def test_ordinary_permission_error_does_not_trigger_fallback(self):
        error = HTTPError(
            "https://api.github.com/", 403, "Forbidden", Message(), io.BytesIO(b"Denied")
        )
        with (
            patch.object(releases, "build_opener") as opener,
            patch.object(releases, "page_release") as fallback,
        ):
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(ValueError, "denied access"):
                releases.latest_release(APP, Event(), self.cache)
            fallback.assert_not_called()
        self.assertEqual(releases._RETRY_AFTER, {})

    def test_public_fallback_requires_published_checksum_and_size(self):
        tag_url = f"https://github.com/{APP.repo}/releases/tag/v{self.release.version}"
        sums = f"{self.release.sha256} *{self.release.name}\n".encode()
        with (
            patch.object(releases, "request") as request,
            patch.object(releases, "read_metadata", return_value=sums) as metadata,
        ):
            request.side_effect = [
                Response(tag_url),
                Response(self.release.url, {"Content-Length": str(self.release.size)}),
            ]
            self.assertEqual(releases.page_release(APP, Event()), self.release)
            request.assert_any_call(self.release.url, method="HEAD")
            metadata.assert_called_once_with(self.release.url.rsplit("/", 1)[0] + "/SHA256SUMS.txt")

    def test_appimage_names_without_linux_work_for_api_fallback_and_cache(self):
        arch = releases.architecture()
        for name in (f"{APP.id}-1.2.3-{arch}.AppImage", f"{APP.id}-{arch}.AppImage"):
            with self.subTest(name=name):
                release = replace(
                    self.release, name=name, url=self.release.url.rsplit("/", 1)[0] + "/" + name
                )
                data = {
                    "tag_name": "v1.2.3",
                    "assets": [
                        {
                            "name": name,
                            "browser_download_url": release.url,
                            "size": release.size,
                            "digest": "sha256:" + release.sha256,
                        }
                    ],
                }
                self.assertEqual(releases.select_release(APP, data, arch), release)
                other_arch = "aarch64" if arch == "x86_64" else "x86_64"
                with self.assertRaisesRegex(ValueError, "AppImage filename"):
                    releases.select_release(APP, data, other_arch)
                tag_url = f"https://github.com/{APP.repo}/releases/tag/v1.2.3"
                with (
                    patch.object(releases, "request") as request,
                    patch.object(
                        releases,
                        "read_metadata",
                        return_value=f"{release.sha256} {name}\n".encode(),
                    ),
                ):
                    request.side_effect = [
                        Response(tag_url),
                        Response(release.url, {"Content-Length": str(release.size)}),
                    ]
                    self.assertEqual(releases.page_release(APP, Event()), release)
                atomic_json(
                    self.cache_file, {"checked_at": time.time(), "release": asdict(release)}
                )
                self.assertEqual(releases.cached_release(APP, self.cache), release)

    def test_checksum_manifest_rejects_conflicting_entries(self):
        line = f"{self.release.sha256} *{self.release.name}\n"
        self.assertEqual(
            releases.checksums("invalid line\n" + line * 2),
            {self.release.name: self.release.sha256},
        )
        with self.assertRaisesRegex(ValueError, "conflicting"):
            releases.checksums(line + f"{'0' * 64} {self.release.name}\n")

    def test_public_fallback_rejects_wrong_repository_missing_checksum_or_size(self):
        tag_url = f"https://github.com/{APP.repo}/releases/tag/v{self.release.version}"
        sums = f"{self.release.sha256} {self.release.name}\n".encode()
        cases = (
            ("https://github.com/someone/else/releases/tag/v1.2.3", sums, "1"),
            (tag_url, b"", "1"),
            (tag_url, sums, ""),
            (tag_url, sums, "0"),
            (tag_url, sums, str(releases.MAX_DOWNLOAD + 1)),
        )
        for location, manifest, length in cases:
            with (
                self.subTest(location=location, manifest=manifest, length=length),
                patch.object(releases, "request") as request,
                patch.object(releases, "read_metadata", return_value=manifest),
            ):
                request.side_effect = [
                    Response(location),
                    Response("unused", {"Content-Length": length}),
                ]
                with self.assertRaises(ValueError):
                    releases.page_release(APP, Event())

    def test_fresh_cache_survives_restart_and_avoids_requests(self):
        with (
            patch.object(releases, "api_release", return_value=self.release) as api,
            patch.object(releases, "page_release") as fallback,
        ):
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)
            releases._RETRY_AFTER.clear()
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)
            api.assert_called_once()
            fallback.assert_not_called()

    def test_expired_or_invalid_cache_is_refetched(self):
        bad_items = (
            {
                "checked_at": time.time() - releases.CACHE_SECONDS - 1,
                "release": asdict(self.release),
            },
            {"checked_at": True, "release": asdict(self.release)},
            {"checked_at": time.time(), "release": []},
            {
                "checked_at": time.time(),
                "release": {**asdict(self.release), "url": "https://evil.test/a"},
            },
            {"checked_at": time.time(), "release": {**asdict(self.release), "sha256": "bad"}},
            {"checked_at": time.time(), "release": {**asdict(self.release), "size": 0}},
            {"checked_at": time.time(), "release": {**asdict(self.release), "version": "9.9.9"}},
        )
        for data in bad_items:
            with self.subTest(data=data):
                atomic_json(self.cache_file, data)
                with patch.object(releases, "api_release", return_value=self.release) as api:
                    self.assertEqual(
                        releases.latest_release(APP, Event(), self.cache), self.release
                    )
                    api.assert_called_once()
        self.cache_file.write_text("not JSON")
        self.assertIsNone(releases.cached_release(APP, self.cache))

    def test_persisted_cooldown_uses_public_fallback_after_restart(self):
        until = time.time() + 600
        releases._RETRY_AFTER["api.github.com"] = until
        with patch.object(releases, "page_release", return_value=self.release):
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)
        self.assertEqual(
            json.loads((self.cache / "limits.json").read_text())["api.github.com"], until
        )
        self.cache_file.unlink()
        releases._RETRY_AFTER.clear()
        with (
            patch.object(releases, "page_release", return_value=self.release) as fallback,
            patch.object(releases, "build_opener") as opener,
        ):
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)
            opener.assert_not_called()
            fallback.assert_called_once()

    def test_cache_write_failure_does_not_block_release_lookup(self):
        with (
            patch.object(releases, "api_release", return_value=self.release),
            patch.object(releases, "atomic_json", side_effect=OSError("Read only")),
        ):
            self.assertEqual(releases.latest_release(APP, Event(), self.cache), self.release)

    def test_expired_cache_does_not_masquerade_as_success_when_offline(self):
        atomic_json(self.cache_file, {"checked_at": 0, "release": asdict(self.release)})
        with patch.object(releases, "api_release", side_effect=OSError("Offline")):
            with self.assertRaisesRegex(OSError, "Offline"):
                releases.latest_release(APP, Event(), self.cache)


if __name__ == "__main__":
    unittest.main()
