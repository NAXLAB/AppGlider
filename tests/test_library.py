"""Install lifecycle and launch proof using temporary files and controlled downloads."""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from appharbour.catalog import APPS
from appharbour.library import Installed, Library, check_appimage, desktop_exec
from appharbour.releases import (
    Cancelled,
    Release,
    architecture,
    download,
    latest_release,
    select_release,
    version_key,
)

APP = APPS[0]


def appimage_bytes(extra: bytes = b"test payload") -> bytes:
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4:6] = b"\x02\x01"
    header[8:11] = b"AI\x02"
    header[18:20] = (62 if architecture() == "x86_64" else 183).to_bytes(2, "little")
    return bytes(header) + extra


def release_for(version: str, data: bytes) -> Release:
    name = f"{APP.id}-{version}-linux-{architecture()}.AppImage"
    return Release(
        version,
        name,
        f"https://github.com/{APP.repo}/releases/download/v{version}/{name}",
        len(data),
        hashlib.sha256(data).hexdigest(),
    )


def ignore_progress(message: str, received: int, total: int) -> None:
    pass


class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.external = self.root / "external"
        self.external.mkdir()
        self.library = Library(
            self.root / "data",
            self.root / "apps",
            desktop_dir=self.root / "desktop",
        )

    def install(self, version: str):
        data = appimage_bytes(version.encode())
        release = release_for(version, data)
        with patch("appharbour.releases.request", return_value=io.BytesIO(data)):
            return self.library.install(APP, release, Event(), ignore_progress)

    def test_unregistered_appimages_are_not_part_of_the_library(self) -> None:
        external = self.external / f"photocraft-1.2.0-linux-{architecture()}.AppImage"
        external.write_bytes(appimage_bytes())
        self.library.apps_dir.mkdir()
        loose = self.library.apps_dir / external.name
        loose.write_bytes(appimage_bytes())
        self.assertEqual(self.library.scan(), {})
        self.assertEqual(external.read_bytes(), appimage_bytes())
        self.assertEqual(loose.read_bytes(), appimage_bytes())
        self.assertFalse(self.library.data_dir.exists())

    def test_legacy_external_link_is_ignored_and_does_not_block_own_install(self) -> None:
        location = self.external / "photocraft.AppImage"
        location.write_bytes(appimage_bytes(b"original external copy"))
        self.library._save({APP.id: Installed(location, None, False)})
        self.assertEqual(self.library.scan(), {})
        installed = self.install("1.0.0")
        reopened = Library(self.root / "data", self.root / "apps")
        self.assertEqual(reopened.scan()[APP.id].path, installed.path)
        self.assertTrue(reopened.scan()[APP.id].managed)
        self.assertEqual(location.read_bytes(), appimage_bytes(b"original external copy"))
        self.library.remove(APP)
        self.assertTrue(location.is_file())
        self.assertEqual(self.library.scan(), {})

    def test_rejects_invalid_or_wrong_architecture_appimages(self) -> None:
        location = self.root / "bad.AppImage"
        for content in (b"not an appimage", appimage_bytes()[:18] + b"\xff\xff"):
            location.write_bytes(content)
            with self.assertRaises(ValueError):
                check_appimage(location)
        self.assertFalse((self.root / "data" / "library.json").exists())

    def test_install_update_rollback_forward_and_remove_keep_user_files(self) -> None:
        first = self.install("1.0.0")
        second = self.install("1.1.0")
        self.assertEqual(second.previous, first.path)
        self.assertTrue(first.path.is_file())
        self.assertTrue(os.access(second.path, os.X_OK))
        self.library.rollback(APP)
        self.assertEqual(self.library.scan()[APP.id].path, first.path)
        # Updating after a rollback should reuse the already verified version, not overwrite it.
        restored = self.install("1.1.0")
        self.assertEqual(restored.path, second.path)
        note = second.path.parent / "user-note.txt"
        note.write_text("keep me")
        document = self.root / "drawing.psd"
        document.write_text("my document")
        self.library.remove(APP)
        self.assertNotIn(APP.id, self.library.scan())
        self.assertFalse(first.path.exists())
        self.assertFalse(second.path.exists())
        self.assertEqual(note.read_text(), "keep me")
        self.assertEqual(document.read_text(), "my document")

    def test_bad_checksum_and_cancellation_keep_current_installation(self) -> None:
        first = self.install("1.0.0")
        data = appimage_bytes(b"new release")
        release = release_for("1.1.0", data)
        for cancelled in (False, True):
            event = Event()
            if cancelled:
                event.set()
            with (
                self.subTest(cancelled=cancelled),
                patch(
                    "appharbour.releases.request",
                    return_value=io.BytesIO(b"x" * len(data)),
                ),
            ):
                with self.assertRaises((ValueError, Cancelled)):
                    self.library.install(APP, release, event, ignore_progress)
                self.assertEqual(self.library.scan()[APP.id].path, first.path)
                self.assertTrue(first.path.is_file())
                self.assertFalse(list(first.path.parent.parent.glob(".download-*")))

    def test_state_write_failure_keeps_old_active_version(self) -> None:
        first = self.install("1.0.0")
        data = appimage_bytes(b"new release")
        with (
            patch("appharbour.releases.request", return_value=io.BytesIO(data)),
            patch.object(self.library, "_save", side_effect=OSError("disk full")),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.library.install(APP, release_for("1.1.0", data), Event(), ignore_progress)
        self.assertEqual(self.library.scan()[APP.id].path, first.path)
        self.assertTrue(first.path.is_file())

    def test_corrupt_state_is_preserved_and_blocks_mutation(self) -> None:
        self.library.data_dir.mkdir()
        state = self.library.data_dir / "library.json"
        state.write_text("broken JSON")
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.install("1.0.0")
        self.assertEqual(state.read_text(), "broken JSON")

    def test_ownership_and_running_checks_protect_installed_files(self) -> None:
        installed = self.install("1.0.0")
        with patch("appharbour.library.running", return_value=True):
            with self.assertRaisesRegex(ValueError, "Close"):
                self.library.remove(APP)
        self.assertTrue(installed.path.exists())
        marker = installed.path.parent.parent / ".appharbour"
        marker.write_text("not ours")
        with self.assertRaisesRegex(ValueError, "not owned"):
            self.library.remove(APP)
        self.assertTrue(installed.path.exists())

    def test_install_refuses_unowned_folder_and_directory_symlink(self) -> None:
        app_dir = self.library.apps_dir / APP.id
        app_dir.mkdir(parents=True)
        sentinel = app_dir / "user-file"
        sentinel.write_text("keep")
        with self.assertRaises((ValueError, OSError)):
            self.install("1.0.0")
        self.assertEqual(sentinel.read_text(), "keep")
        sentinel.unlink()
        app_dir.rmdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / ".appharbour").write_text(self.library._marker(APP))
        app_dir.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic"):
            self.install("1.0.0")
        self.assertFalse((elsewhere / "1.0.0").exists())

    def test_failed_download_does_not_trust_an_incomplete_file(self) -> None:
        data = appimage_bytes()
        release = release_for("1.0.0", data)
        with patch("appharbour.releases.request", return_value=io.BytesIO(data[:20])):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                download(release, self.root / "download", Event(), ignore_progress)

    def test_launch_uses_exact_argv_and_clears_bundled_environment(self) -> None:
        installed = self.install("1.0.0")
        with (
            patch.dict(
                os.environ,
                {
                    "APPIMAGE": "/launcher.AppImage",
                    "QT_PLUGIN_PATH": "/launcher/qt",
                    "APPDIR": "/launcher",
                    "SSL_CERT_FILE": "/launcher/certifi/cacert.pem",
                    "SSL_CERT_DIR": "/etc/ssl/certs",
                },
            ),
            patch("appharbour.library.subprocess.Popen") as spawn,
        ):
            spawn.return_value.poll.return_value = None
            self.library.launch(APP, installed)
            self.assertEqual(spawn.call_args.args[0], [str(installed.path)])
            self.assertNotIn("APPIMAGE", spawn.call_args.kwargs["env"])
            self.assertNotIn("QT_PLUGIN_PATH", spawn.call_args.kwargs["env"])
            self.assertNotIn("SSL_CERT_FILE", spawn.call_args.kwargs["env"])
            self.assertEqual(spawn.call_args.kwargs["env"]["SSL_CERT_DIR"], "/etc/ssl/certs")
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            self.assertNotIn("shell", spawn.call_args.kwargs)

    def test_launch_smoke_with_harmless_native_executable(self) -> None:
        data = bytearray(Path(shutil.which("true")).read_bytes())
        data[8:11] = b"AI\x02"
        payload = bytes(data)
        with patch("appharbour.releases.request", return_value=io.BytesIO(payload)):
            installed = self.library.install(
                APP, release_for("1.0.0", payload), Event(), ignore_progress
            )
        self.library.launch(APP, installed)
        self.assertEqual(self.library.processes[APP.id].wait(timeout=5), 0)

    def test_launch_refuses_legacy_external_link(self) -> None:
        path = self.external / "photocraft.AppImage"
        path.write_bytes(appimage_bytes())
        with self.assertRaisesRegex(ValueError, "Only apps installed"):
            self.library.launch(APP, Installed(path, None, False))

    def test_menu_shortcut_tracks_active_version_without_duplicates(self) -> None:
        first = self.install("1.0.0")
        shortcut = self.library.desktop_dir / f"appharbour-{APP.id}.desktop"
        self.assertIn("Name=PhotoCraft (AppHarbour)", shortcut.read_text())
        self.assertIn(str(first.path), shortcut.read_text())
        second = self.install("1.1.0")
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [shortcut])
        self.assertIn(str(second.path), shortcut.read_text())
        self.assertNotIn(str(first.path), shortcut.read_text())
        self.library.rollback(APP)
        self.assertIn(str(first.path), shortcut.read_text())
        shortcut.unlink()
        self.library.create_shortcut(APP)
        self.assertIn(str(first.path), shortcut.read_text())
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [shortcut])

    def test_launcher_shortcut_is_stable_and_preserves_foreign_entries(
        self,
    ) -> None:
        shortcut = self.library.create_launcher_shortcut([sys.executable, "-O", "-m", "appharbour"])
        self.assertIn("Name=AppHarbour\n", shortcut.read_text())
        self.assertIn(
            desktop_exec([sys.executable, "-O", "-m", "appharbour"]), shortcut.read_text()
        )
        self.assertTrue((self.library.data_dir / "icons" / "appharbour.png").is_file())
        self.assertEqual(
            self.library.create_launcher_shortcut([sys.executable, "-O", "-m", "appharbour"]),
            shortcut,
        )
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [shortcut])
        shortcut.write_text("[Desktop Entry]\nName=Someone else's shortcut\n")
        with self.assertRaisesRegex(OSError, "another application"):
            self.library.create_launcher_shortcut([sys.executable, "-O", "-m", "appharbour"])
        self.assertIn("Someone else's shortcut", shortcut.read_text())

    def test_packaged_shortcut_accepts_image_path_with_spaces(self) -> None:
        shortcut = self.library.create_launcher_shortcut(["/home/a b/AppHarbour.AppImage"])
        self.assertIn('Exec="/home/a b/AppHarbour.AppImage"\n', shortcut.read_text())

    def test_shortcut_commands_quote_special_characters_and_reject_control_characters(self) -> None:
        self.assertEqual(desktop_exec(["/a path/100%/app", "-m"]), '"/a path/100%%/app" "-m"')
        self.assertEqual(desktop_exec(['a"$`\\b']), '"a\\\\"\\\\$\\\\`\\\\\\\\b"')
        with self.assertRaisesRegex(ValueError, "control characters"):
            desktop_exec(["/bad\npath"])

    def test_reopen_preserves_library_and_shortcut_ownership(self) -> None:
        installed = self.install("1.0.0")
        marker = installed.path.parent.parent / ".appharbour"
        self.assertEqual(marker.read_text(), f"AppHarbour\n{APP.id}\n{APP.repo}\n")
        state = (self.library.data_dir / "library.json").read_bytes()
        shortcut = self.library.desktop_dir / f"appharbour-{APP.id}.desktop"
        shortcut.write_text(
            shortcut.read_text().replace("PhotoCraft (AppHarbour)", "Outdated application label")
        )
        reopened = Library(
            self.library.data_dir, self.library.apps_dir, desktop_dir=self.library.desktop_dir
        )
        self.assertEqual(reopened.scan()[APP.id], installed)
        reopened.create_shortcut(APP)
        self.assertIn("Name=PhotoCraft (AppHarbour)", shortcut.read_text())
        self.assertEqual((self.library.data_dir / "library.json").read_bytes(), state)
        self.assertEqual(list(self.library.desktop_dir.glob("*.desktop")), [shortcut])

    def test_shortcut_repair_and_uninstall_preserve_other_shortcuts(self) -> None:
        self.install("1.0.0")
        own = self.library.desktop_dir / f"appharbour-{APP.id}.desktop"
        own.write_text("[Desktop Entry]\nName=Someone else's shortcut\n")
        contents = own.read_text()
        with self.assertRaisesRegex(OSError, "not owned"):
            self.library.create_shortcut(APP)
        original = self.library.desktop_dir / "ai.storyteller.photocraft.desktop"
        original.write_text("[Desktop Entry]\nName=PhotoCraft from another installer\n")
        self.library.remove(APP)
        self.assertEqual(own.read_text(), contents)
        self.assertIn("another installer", original.read_text())


class ReleaseTests(unittest.TestCase):
    def metadata(self) -> dict:
        data = appimage_bytes()
        release = release_for("1.2.3", data)
        return {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": release.name,
                    "browser_download_url": release.url,
                    "size": len(data),
                    "digest": "sha256:" + release.sha256,
                },
            ],
        }

    def test_selects_only_correct_architecture_and_repository(self) -> None:
        metadata = self.metadata()
        selected = select_release(APP, metadata, architecture())
        self.assertEqual(selected.version, "1.2.3")
        with self.assertRaisesRegex(ValueError, "No supported wrong-arch AppImage filename"):
            select_release(APP, metadata, "wrong-arch")
        metadata["assets"][0]["browser_download_url"] = "https://evil.example/payload.AppImage"
        with self.assertRaisesRegex(ValueError, "repository"):
            select_release(APP, metadata, architecture())

    def test_checksum_fallback_matches_filename_not_another_asset(self) -> None:
        metadata = self.metadata()
        expected = metadata["assets"][0].pop("digest").removeprefix("sha256:")
        filename = metadata["assets"][0]["name"]
        with patch(
            "appharbour.releases.read_metadata",
            side_effect=[json.dumps(metadata).encode(), f"{expected}  {filename}\n".encode()],
        ):
            self.assertEqual(latest_release(APP, Event()).sha256, expected)
        with patch(
            "appharbour.releases.read_metadata",
            side_effect=[
                json.dumps(metadata).encode(),
                f"{expected}  other-file.AppImage\n".encode(),
            ],
        ):
            with self.assertRaisesRegex(ValueError, "checksum"):
                latest_release(APP, Event())

    def test_rejects_prereleases_unsafe_tags_and_bad_size(self) -> None:
        for key, value in (("draft", True), ("prerelease", True), ("tag_name", "../evil")):
            metadata = self.metadata()
            metadata[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                select_release(APP, metadata, architecture())
        metadata = self.metadata()
        metadata["assets"][0]["size"] = -1
        with self.assertRaises(ValueError):
            select_release(APP, metadata, architecture())

    def test_versions_compare_numerically_and_do_not_guess_unknown_versions(self) -> None:
        self.assertGreater(version_key("1.10.0"), version_key("1.9.0"))
        self.assertGreater(version_key("1.0.0"), version_key("1.0.0-rc.1"))
        self.assertFalse(release_for("1.0.0", appimage_bytes()).newer_than(None))


if __name__ == "__main__":
    unittest.main()
