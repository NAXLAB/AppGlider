"""Repository images are bounded, self-contained and cached without touching app files."""

import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from appharbour import APP_ICON
from appharbour.catalog import APPS, custom_app
from appharbour.icons import MAX_ICON_BYTES, discover_icons, normalise_icon
from appharbour.library import Library
from appharbour.releases import Cancelled, RateLimited

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
    b'<path d="M0 0H64V64H0Z" fill="#39b3d7"/></svg>'
)
CUSTOM = custom_app("owner/example-app")


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data", self.root / "apps", self.root / "desktop")

    def test_png_and_svg_are_normalised_to_bounded_png(self):
        for data in (SVG, APP_ICON.read_bytes()):
            with self.subTest(kind=data[:4]):
                png = normalise_icon(data)
                image = QImage.fromData(png, "PNG")
                self.assertFalse(image.isNull())
                self.assertEqual(max(image.width(), image.height()), 512)
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_invalid_large_and_external_resource_images_are_rejected(self):
        invalid = [
            b"",
            b"not an image",
            b"x" * (MAX_ICON_BYTES + 1),
            SVG.replace(b'width="64"', b'width="100000"'),
            SVG.replace(b"<path", b'<image href="file:///etc/passwd"/><path'),
            SVG.replace(b"<path", b'<image href="https://example.com/icon.png"/><path'),
            b'<!DOCTYPE svg [<!ENTITY x "hello">]>' + SVG,
            SVG.replace(b"<path", b'<style>@import "https://example.com/a.css";</style><path'),
            SVG.replace(b'fill="#39b3d7"', b'fill="url(file:///tmp/image)"'),
        ]
        for data in invalid:
            with self.subTest(data=data[:80]), self.assertRaises(ValueError):
                normalise_icon(data)

    def metadata(self, files):
        blobs = {}
        entries = []
        for path, data in files.items():
            sha = hashlib.sha1(data).hexdigest()
            blobs[sha] = data
            entries.append(
                {"path": path, "sha": sha, "mode": "100644", "type": "blob", "size": len(data)}
            )

        def read(url):
            if url.endswith("/git/trees/HEAD?recursive=1"):
                return json.dumps({"tree": entries, "truncated": False}).encode()
            data = blobs[url.rsplit("/", 1)[-1]]
            return json.dumps(
                {"encoding": "base64", "content": base64.encodebytes(data).decode()}
            ).encode()

        return entries, read

    def test_discovery_prefers_desktop_icon_and_skips_symlinks_and_unrelated_images(self):
        entries, read = self.metadata(
            {
                "packaging/application.desktop": b"[Desktop Entry]\nIcon=actual-app\n",
                "assets/logo.svg": SVG.replace(b"39b3d7", b"112233"),
                "assets/linux/hicolor/512x512/apps/actual-app.svg": SVG,
                "assets/screenshot.svg": SVG,
                "assets/linked-icon.svg": SVG,
            }
        )
        entries[-1]["mode"] = "120000"
        with patch("appharbour.icons.read_metadata", side_effect=read) as metadata:
            result = discover_icons(CUSTOM, Event())
        self.assertEqual(
            [icon.name for icon in result],
            ["assets/linux/hicolor/512x512/apps/actual-app.svg", "assets/logo.svg"],
        )
        self.assertTrue(all(icon.png.startswith(b"\x89PNG") for icon in result))
        self.assertTrue(
            all(
                call.args[0].startswith("https://api.github.com/repos/owner/example-app/")
                for call in metadata.call_args_list
            )
        )

    def test_bad_candidates_and_incomplete_trees_are_bounded(self):
        entries, read = self.metadata(
            {f"assets/icon-{n}.png": b"broken" + bytes([n]) for n in range(20)}
        )
        with patch("appharbour.icons.read_metadata", side_effect=read) as metadata:
            self.assertEqual(discover_icons(CUSTOM, Event()), ())
            self.assertEqual(metadata.call_count, 9)
        for tree in ({"tree": entries, "truncated": True}, [], {"tree": "bad"}):
            with patch("appharbour.icons.read_metadata", return_value=json.dumps(tree).encode()):
                with self.assertRaises(ValueError):
                    discover_icons(CUSTOM, Event())
        cancel = Event()
        cancel.set()
        with patch("appharbour.icons.read_metadata") as metadata:
            with self.assertRaises(Cancelled):
                discover_icons(CUSTOM, cancel)
            metadata.assert_not_called()
        with patch("appharbour.icons.read_metadata", side_effect=RateLimited("api.github.com", 1)):
            with self.assertRaises(RateLimited):
                discover_icons(CUSTOM, Event())

    def test_saved_icon_survives_restart_and_failed_replacement(self):
        self.library.add_repository(CUSTOM)
        self.library.set_icon(CUSTOM, SVG)
        path = self.library.icon_path(CUSTOM)
        original = path.read_bytes()
        self.assertNotEqual(path, APP_ICON)
        reopened = Library(self.library.data_dir, self.library.apps_dir, self.library.desktop_dir)
        self.assertEqual(reopened.icon_path(CUSTOM), path)
        self.assertEqual(reopened.apps[-1], CUSTOM)
        with patch("appharbour.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                reopened.set_icon(CUSTOM, SVG.replace(b"39b3d7", b"112233"))
        with self.assertRaises(ValueError):
            reopened.set_icon(CUSTOM, b"not an image")
        self.assertEqual(path.read_bytes(), original)
        self.library.remove_repository(CUSTOM.id)
        self.assertFalse(path.exists())

    def test_builtin_unknown_and_symlinked_icon_locations_are_preserved(self):
        for app in (APPS[0], CUSTOM):
            with self.assertRaisesRegex(ValueError, "Only added"):
                self.library.set_icon(app, SVG)
        self.library.add_repository(CUSTOM)
        external = self.root / "external"
        external.mkdir()
        directory = self.library.data_dir / "icons"
        directory.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.library.set_icon(CUSTOM, SVG)
        self.assertEqual(self.library.icon_path(CUSTOM), APP_ICON)
        self.assertEqual(list(external.iterdir()), [])
