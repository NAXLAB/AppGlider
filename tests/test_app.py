"""Packaged and development entry points keep stable launch commands."""

import os
import unittest
from unittest.mock import patch

from appharbour.app import launcher_command


class EntryPointTests(unittest.TestCase):
    def test_packaged_shortcut_points_at_original_image_with_spaces(self) -> None:
        with (
            patch.dict(
                os.environ, {"APPDIR": "/tmp/mount", "APPIMAGE": "/home/a b/AppHarbour.AppImage"}
            ),
            patch("appharbour.app.sys.executable", "/tmp/mount/opt/python/bin/python3.13"),
            patch("appharbour.app.sys.prefix", "/tmp/mount/opt/python"),
        ):
            self.assertEqual(launcher_command(), ["/home/a b/AppHarbour.AppImage"])

    def test_foreign_appimage_environment_does_not_replace_development_command(self) -> None:
        with (
            patch.dict(os.environ, {"APPDIR": "/tmp/other", "APPIMAGE": "/other.AppImage"}),
            patch("appharbour.app.sys.executable", "/project/.venv/bin/python"),
            patch("appharbour.app.sys.prefix", "/project/.venv"),
        ):
            self.assertEqual(
                launcher_command(), ["/project/.venv/bin/python", "-O", "-m", "appharbour"]
            )
