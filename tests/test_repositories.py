"""Custom repositories persist safely and join the existing app lifecycle."""

import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_library import appimage_bytes

from appharbour import APP_ICON
from appharbour.catalog import APPS, custom_app, load_apps
from appharbour.icons import IconCandidate, normalise_icon
from appharbour.library import Library
from appharbour.releases import Release, architecture, check_cancel, select_release
from appharbour.repositories_dialog import RepositoriesDialog
from appharbour.window import LauncherWindow, SettingsDialog

CUSTOM = custom_app("https://github.com/emmertex/lightcraft-classic", "LightCraft Classic")


def custom_release(version="0.9.4", payload=None):
    payload = payload if payload is not None else appimage_bytes()
    name = f"{CUSTOM.asset_stem}-{version}-linux-{architecture()}.AppImage"
    return Release(
        version,
        name,
        f"https://github.com/{CUSTOM.repo}/releases/download/v{version}/{name}",
        len(payload),
        hashlib.sha256(payload).hexdigest(),
    )


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data", self.root / "apps", self.root / "desktop")

    def install(self, version):
        payload = appimage_bytes(version.encode())
        with patch("appharbour.releases.request", return_value=io.BytesIO(payload)):
            return self.library.install(
                CUSTOM, custom_release(version, payload), Event(), lambda *a: None
            )

    def test_repository_addresses_are_normalized_and_ids_do_not_collide(self):
        self.assertEqual(
            custom_app("  https://github.com/Emmertex/Lightcraft-Classic.git/ ", CUSTOM.name),
            CUSTOM,
        )
        self.assertEqual(custom_app("emmertex/lightcraft-classic", CUSTOM.name), CUSTOM)
        other = custom_app("another-owner/lightcraft-classic")
        self.assertNotEqual(CUSTOM.id, other.id)
        self.assertEqual(CUSTOM.asset_stem, other.asset_stem)
        self.assertEqual(CUSTOM.icon, APP_ICON)

    def test_invalid_sources_names_and_path_traversal_are_rejected(self):
        for address in (
            "http://github.com/owner/app",
            "https://github.com.evil.test/owner/app",
            "https://user@github.com/owner/app",
            "https://github.com:443/owner/app",
            "https://github.com/owner/app?download=true",
            "https://github.com/owner/app#readme",
            "https://github.com/owner/app/releases",
            "owner/..",
            "../app",
            "owner/app/../other",
            "owner/ap\np",
            "owner/app%2fother",
            "",
            "owner",
            "owner/app\\other",
        ):
            with self.subTest(address=address), self.assertRaises(ValueError):
                custom_app(address)
        for name in ("", " ", "bad\nname", "x" * 129):
            with self.subTest(name=name), self.assertRaises(ValueError):
                custom_app(CUSTOM.repo, name)

    def test_catalog_persists_without_altering_builtin_catalog_or_library(self):
        self.assertEqual(self.library.apps, APPS)
        self.assertFalse(self.library.data_dir.exists())
        self.library.add_repository(CUSTOM)
        self.assertEqual(self.library.apps, (*APPS, CUSTOM))
        reopened = Library(self.library.data_dir, self.library.apps_dir)
        self.assertEqual(reopened.apps[-1], CUSTOM)
        self.assertFalse((self.library.data_dir / "library.json").exists())
        self.assertEqual(len(APPS), 14)
        self.library.remove_repository(CUSTOM.id)
        self.assertEqual(reopened.apps, APPS)

    def test_duplicates_and_builtin_removal_are_rejected(self):
        self.library.add_repository(CUSTOM)
        for app in (
            CUSTOM,
            custom_app("EMMERTEX/LIGHTCRAFT-CLASSIC", "Other name"),
            custom_app(APPS[0].repo),
        ):
            with self.subTest(repo=app.repo), self.assertRaisesRegex(ValueError, "already"):
                self.library.add_repository(app)
        with self.assertRaisesRegex(ValueError, "Only custom"):
            self.library.remove_repository(APPS[0].id)
        with self.assertRaisesRegex(ValueError, "Invalid"):
            self.library.add_repository(replace(CUSTOM, id="../../escape"))

    def test_corrupt_catalog_is_preserved_and_blocks_mutations(self):
        self.library.add_repository(CUSTOM)
        path = self.library.data_dir / "repositories.json"
        for content in (
            "broken JSON",
            "{}",
            '[{"repo": "owner/app"}]',
            '[{"repo": "../app", "name": "Bad"}]',
        ):
            path.write_text(content)
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "preserved"):
                self.library.add_repository(CUSTOM)
            self.assertEqual(path.read_text(), content)
        path.write_text(json.dumps([{"repo": CUSTOM.repo, "name": CUSTOM.name}] * 2))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_apps(path)

    def test_failed_catalog_save_keeps_original_file(self):
        self.library.add_repository(CUSTOM)
        path = self.library.data_dir / "repositories.json"
        original = path.read_bytes()
        with patch("appharbour.storage.os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.library.add_repository(custom_app("other/another-app"))
        self.assertEqual(path.read_bytes(), original)

    def test_custom_install_update_rollback_restart_and_remove_preserve_ownership(self):
        with self.assertRaisesRegex(ValueError, "Add this repository"):
            self.install("0.9.3")
        self.library.add_repository(CUSTOM)
        first = self.install("0.9.3")
        second = self.install("0.9.4")
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(second.path.parent.parent.name, CUSTOM.id)
        self.assertTrue((second.path.parent.parent / ".appharbour").is_file())
        with self.assertRaisesRegex(ValueError, "Uninstall"):
            self.library.remove_repository(CUSTOM.id)
        self.library.rollback(CUSTOM)
        reopened = Library(self.library.data_dir, self.library.apps_dir, self.library.desktop_dir)
        self.assertEqual(reopened.scan()[CUSTOM.id].path, first.path)
        shortcut = self.library.desktop_dir / f"appharbour-{CUSTOM.id}.desktop"
        self.assertIn("LightCraft Classic (AppHarbour)", shortcut.read_text())
        self.library.remove(CUSTOM)
        self.library.remove_repository(CUSTOM.id)
        self.assertEqual(reopened.scan(), {})
        self.assertEqual(reopened.apps, APPS)
        self.assertFalse(shortcut.exists())

    def test_custom_filename_matching_is_separate_from_storage_id(self):
        release = custom_release()
        data = {
            "tag_name": "v0.9.4",
            "assets": [
                {
                    "name": release.name,
                    "browser_download_url": release.url.replace("emmertex", "Emmertex"),
                    "size": release.size,
                    "digest": "sha256:" + release.sha256,
                }
            ],
        }
        selected = select_release(CUSTOM, data, architecture())
        self.assertEqual(selected.name, release.name)
        for asset in data["assets"]:
            asset["name"] = asset["name"].replace("-linux-", "-")
            asset["browser_download_url"] = asset["browser_download_url"].replace("-linux-", "-")
        self.assertEqual(
            select_release(CUSTOM, data, architecture()).name, release.name.replace("-linux-", "-")
        )
        data["assets"][0]["browser_download_url"] = release.url.replace(CUSTOM.repo, "another/app")
        with self.assertRaisesRegex(ValueError, "repository"):
            select_release(CUSTOM, data, architecture())


class RepositoryGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data", self.root / "apps", self.root / "desktop")
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        icons = patch("appharbour.repositories_dialog.discover_icons", return_value=())
        icons.start()
        self.addCleanup(icons.stop)
        self.settings.setValue("appearance/theme", "Light")
        self.window = LauncherWindow(self.settings, self.library)
        self.window.show()
        self.addCleanup(self.window.close)
        self.dialog = RepositoriesDialog(self.library, self.window)
        self.dialog.catalog_changed.connect(self.window.scan_installed)
        self.dialog.show()
        self.qt.processEvents()
        self.addCleanup(self.dialog.close)

    def wait_for_task(self, owner):
        deadline = time.monotonic() + 5
        while owner.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(owner.task, "Background operation did not finish")

    def inspect(self):
        if not self.dialog.adding:
            QTest.mouseClick(self.dialog.new_repository_button, Qt.MouseButton.LeftButton)
        self.dialog.url.setText(CUSTOM.source_url)
        self.dialog.name.setText(CUSTOM.name)
        with patch(
            "appharbour.repositories_dialog.latest_release", return_value=custom_release()
        ) as latest:
            QTest.mouseClick(self.dialog.inspect_button, Qt.MouseButton.LeftButton)
            self.wait_for_task(self.dialog)
            latest.assert_called_once()
            self.assertEqual(latest.call_args.args[0], CUSTOM)
        self.assertTrue(self.dialog.add_button.isEnabled())

    def test_repository_views_keep_addition_and_existing_app_actions_separate(self):
        self.assertTrue(self.dialog.details_page.isVisibleTo(self.dialog))
        self.assertFalse(self.dialog.add_page.isVisibleTo(self.dialog))
        self.assertTrue(self.dialog.remove_button.isHidden())
        self.assertTrue(self.dialog.icon_group.isHidden())
        QTest.mouseClick(self.dialog.new_repository_button, Qt.MouseButton.LeftButton)
        self.assertTrue(self.dialog.add_page.isVisibleTo(self.dialog))
        self.assertTrue(self.dialog.release_preview.isHidden())
        self.assertTrue(self.dialog.icon_group.isHidden())
        self.inspect()
        self.assertTrue(self.dialog.release_preview.isVisible())
        self.assertTrue(self.dialog.icon_group.isVisible())
        self.dialog.add_repository()
        self.assertTrue(self.dialog.details_page.isVisibleTo(self.dialog))
        self.assertEqual(self.dialog.detail_name.text(), CUSTOM.name)
        self.assertTrue(self.dialog.add_button.isHidden())
        self.assertTrue(self.dialog.release_preview.isHidden())
        self.assertTrue(self.dialog.remove_button.isVisible())
        self.assertTrue(self.dialog.icon_group.isVisible())
        self.assertFalse(self.dialog.url.isVisibleTo(self.dialog))

    def select_custom(self):
        for row in range(self.dialog.repo_list.count()):
            if self.dialog.repo_list.item(row).data(Qt.ItemDataRole.UserRole) == CUSTOM.id:
                self.dialog.repo_list.setCurrentRow(row)
                return
        self.fail("Custom repository missing from list")

    def test_settings_button_opens_repository_management(self):
        settings = SettingsDialog(self.settings, self.window)
        self.addCleanup(settings.close)
        with patch.object(self.window, "open_repositories") as open_repositories:
            settings.repositories_requested.connect(self.window.open_repositories)
            QTest.mouseClick(settings.repositories_button, Qt.MouseButton.LeftButton)
            open_repositories.assert_called_once()

    def test_repository_screen_opens_through_the_actual_settings_dialog(self):
        observed = []

        def close_repositories():
            active = self.qt.activeModalWidget()
            if isinstance(active, RepositoriesDialog):
                observed.append(active.windowTitle())
                active.reject()
            elif isinstance(active, SettingsDialog):
                active.reject()

        def open_repositories():
            active = self.qt.activeModalWidget()
            if isinstance(active, SettingsDialog):
                QTimer.singleShot(0, close_repositories)
                QTest.mouseClick(active.repositories_button, Qt.MouseButton.LeftButton)
                active.reject()

        QTimer.singleShot(0, open_repositories)
        self.window.open_settings()
        self.assertEqual(observed, ["Repositories"])

    def test_inspect_preview_add_and_restart_update_library_immediately(self):
        self.inspect()
        self.assertIn("v0.9.4", self.dialog.version.text())
        self.assertEqual(self.dialog.filename.text(), custom_release().name)
        self.assertIn("SHA-256", self.dialog.checksum.text())
        QTest.mouseClick(self.dialog.add_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.window.cards), 15)
        self.assertEqual(self.window.tabs.tabText(0), "All apps")
        self.assertEqual(self.window.cards[CUSTOM.id].app.name, CUSTOM.name)
        self.assertFalse(self.dialog.add_button.isEnabled())
        self.assertEqual(self.window.cards[CUSTOM.id].action_button.text(), "Install")
        reopened = LauncherWindow(
            self.settings, Library(self.library.data_dir, self.library.apps_dir)
        )
        self.addCleanup(reopened.close)
        self.assertIn(CUSTOM.id, reopened.cards)
        self.assertEqual(reopened.tabs.tabText(0), "All apps")
        self.assertIn("lightcraft", reopened.cards)
        self.select_custom()
        QTest.mouseClick(self.dialog.remove_button, Qt.MouseButton.LeftButton)
        self.assertNotIn(CUSTOM.id, self.window.cards)
        self.assertEqual(len(self.window.cards), 14)
        self.assertEqual(self.window.tabs.tabText(0), "All apps")

    def test_edits_invalidate_preview_and_invalid_or_duplicate_sources_do_not_fetch(self):
        self.inspect()
        self.dialog.name.setText("Changed name")
        self.assertFalse(self.dialog.add_button.isEnabled())
        self.assertIsNone(self.dialog.release)
        for address in ("https://evil.test/a/b", APPS[0].source_url):
            self.dialog.url.setText(address)
            with patch("appharbour.repositories_dialog.latest_release") as latest:
                self.dialog.inspect_repository()
                latest.assert_not_called()
            self.assertIsNone(self.dialog.task)
            self.assertFalse(self.dialog.add_button.isEnabled())

    def test_missing_appimage_or_checksum_is_reported_without_saving(self):
        self.dialog.url.setText(CUSTOM.source_url)
        with patch(
            "appharbour.repositories_dialog.latest_release",
            side_effect=ValueError("No published SHA-256 checksum"),
        ):
            self.dialog.inspect_repository()
            self.wait_for_task(self.dialog)
        self.assertIn("No published SHA-256", self.dialog.status.text())
        self.assertFalse(self.dialog.add_button.isEnabled())
        self.assertFalse((self.library.data_dir / "repositories.json").exists())

    def test_discovered_icon_preview_selection_persistence_and_shortcuts(self):
        first = normalise_icon(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
            b'<path d="M0 0H32V32H0Z" fill="red"/></svg>'
        )
        second = normalise_icon(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
            b'<path d="M0 0H32V32H0Z" fill="blue"/></svg>'
        )
        choices = (IconCandidate("assets/red.svg", first), IconCandidate("assets/blue.svg", second))
        with patch("appharbour.repositories_dialog.discover_icons", return_value=choices):
            self.inspect()
        self.assertFalse(self.dialog.icon_preview.pixmap().isNull())
        self.dialog.icon_choices.setCurrentIndex(1)
        self.assertEqual(self.dialog.selected_icon, second)
        self.dialog.add_repository()
        cached = self.library.icon_path(CUSTOM)
        self.assertEqual(cached.read_bytes(), second)
        self.assertEqual(
            self.window.cards[CUSTOM.id].icon.pixmap().toImage().pixelColor(0, 0).name(), "#0000ff"
        )
        payload = appimage_bytes()
        with patch("appharbour.releases.request", return_value=io.BytesIO(payload)):
            self.library.install(CUSTOM, custom_release(payload=payload), Event(), lambda *a: None)
        self.window.scan_installed()
        shortcut = self.library.desktop_dir / f"appharbour-{CUSTOM.id}.desktop"
        self.assertIn(f"Icon={cached}", shortcut.read_text())
        self.assertEqual(cached.read_bytes(), second)
        self.assertFalse(self.window.tray_apps_menu.actions()[0].icon().isNull())
        self.select_custom()
        with patch("appharbour.repositories_dialog.discover_icons", return_value=(choices[0],)):
            self.dialog.find_existing_icons()
            self.wait_for_task(self.dialog)
        self.assertEqual(cached.read_bytes(), first)
        self.assertEqual(
            self.window.cards[CUSTOM.id].icon.pixmap().toImage().pixelColor(0, 0).name(), "#ff0000"
        )
        self.assertEqual(
            Library(self.library.data_dir, self.library.apps_dir).icon_path(CUSTOM).read_bytes(),
            first,
        )

    def test_icon_lookup_failure_still_allows_add_and_local_choice_for_existing_repo(self):
        with patch(
            "appharbour.repositories_dialog.discover_icons", side_effect=ValueError("quota")
        ):
            self.inspect()
        self.dialog.add_repository()
        self.assertIn(CUSTOM, self.library.apps)
        self.select_custom()
        png = normalise_icon(APP_ICON.read_bytes())
        image = self.root / "a local icon.png"
        image.write_bytes(png)
        with patch(
            "appharbour.repositories_dialog.QFileDialog.getOpenFileName",
            return_value=(str(image), ""),
        ):
            self.dialog.choose_icon()
        cached = self.library.icon_path(CUSTOM)
        saved = cached.read_bytes()
        image.write_bytes(b"broken PNG")
        with patch(
            "appharbour.repositories_dialog.QFileDialog.getOpenFileName",
            return_value=(str(image), ""),
        ):
            self.dialog.choose_icon()
        self.assertEqual(cached.read_bytes(), saved)
        self.assertIn("valid PNG or SVG", self.dialog.status.text())

    def test_close_cancels_inspection_and_waits_for_worker(self):
        self.dialog.url.setText(CUSTOM.source_url)

        def lookup(app, cancel, cache):
            cancel.wait(timeout=1)
            check_cancel(cancel)
            return custom_release()

        with patch("appharbour.repositories_dialog.latest_release", side_effect=lookup):
            self.dialog.inspect_repository()
            task = self.dialog.task
            self.dialog.reject()
            self.assertTrue(task.cancel.is_set())
            self.wait_for_task(self.dialog)
        self.assertFalse(self.dialog.isVisible())
        self.assertEqual(self.library.apps, APPS)

    def test_close_cancels_icon_lookup_for_an_existing_repository(self):
        self.library.add_repository(CUSTOM)
        self.dialog.refresh_list()
        self.select_custom()

        def lookup(app, cancel):
            cancel.wait(timeout=1)
            check_cancel(cancel)
            return ()

        with patch("appharbour.repositories_dialog.discover_icons", side_effect=lookup):
            self.dialog.find_existing_icons()
            task = self.dialog.task
            self.dialog.reject()
            self.assertTrue(task.cancel.is_set())
            self.wait_for_task(self.dialog)
        self.assertFalse(self.dialog.isVisible())
        self.assertEqual(self.library.icon_path(CUSTOM), APP_ICON)

    def test_icon_save_failure_does_not_undo_repository_addition(self):
        png = normalise_icon(APP_ICON.read_bytes())
        with patch(
            "appharbour.repositories_dialog.discover_icons",
            return_value=(IconCandidate("icon.png", png),),
        ):
            self.inspect()
        with patch.object(self.library, "set_icon", side_effect=OSError("Disk full")):
            self.dialog.add_repository()
        self.assertIn(CUSTOM, self.library.apps)
        self.assertIn("Icon could not be saved", self.dialog.status.text())
        self.assertIn(CUSTOM.id, self.window.cards)

    def test_custom_app_install_update_tray_and_repository_removal_guard(self):
        self.inspect()
        self.dialog.add_repository()
        payload = appimage_bytes()
        with (
            patch("appharbour.window.latest_release", return_value=custom_release()),
            patch("appharbour.releases.request", return_value=io.BytesIO(payload)),
            patch("appharbour.window.QMessageBox.warning") as warning,
        ):
            self.window.install_app(CUSTOM.id)
            self.wait_for_task(self.window)
            warning.assert_not_called()
        self.assertEqual(self.window.cards[CUSTOM.id].action_button.text(), "Launch")
        self.assertEqual(self.window.tabs.tabText(1), "Installed (1)")
        self.assertIn(
            CUSTOM.name, [action.text() for action in self.window.tray_apps_menu.actions()]
        )
        self.select_custom()
        self.dialog.remove_repository()
        self.assertIn("Uninstall", self.dialog.status.text())
        self.assertIn(CUSTOM, self.library.apps)
        with patch(
            "appharbour.window.latest_release", return_value=custom_release("0.9.5")
        ) as latest:
            self.window.check_updates()
            self.wait_for_task(self.window)
            latest.assert_called_once()
        self.assertEqual(self.window.tabs.tabText(2), "Updates (1)")
        with (
            patch("appharbour.window.latest_release", return_value=custom_release("0.9.5")),
            patch("appharbour.releases.request", return_value=io.BytesIO(payload)),
        ):
            self.window.update_all()
            self.wait_for_task(self.window)
        self.assertEqual(self.window.installed[CUSTOM.id].version, "0.9.5")
        with patch(
            "appharbour.window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.remove_app(CUSTOM.id)
            self.wait_for_task(self.window)
        self.assertEqual(self.window.tabs.tabText(1), "Installed (0)")
        self.dialog.remove_repository()
        self.assertNotIn(CUSTOM.id, self.window.cards)
        self.assertEqual(self.library.scan(), {})


if __name__ == "__main__":
    unittest.main()
