"""Exercise the actual Qt widgets without network or installation side effects."""

import io
import os
import tempfile
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QImage, QPalette, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionFrame,
    QSystemTrayIcon,
    QToolButton,
)
from test_library import APP, appimage_bytes, release_for

from appharbour import APP_ICON, APP_VERSION
from appharbour.catalog import APPS
from appharbour.jobs import TaskResult
from appharbour.library import Installed, Library
from appharbour.release_info import ReleaseInfo, ReleaseOption, info_path
from appharbour.releases import Release
from appharbour.repositories_dialog import RepositoriesDialog
from appharbour.storage import atomic_json
from appharbour.window import DetailsDialog, LauncherWindow, SettingsDialog


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setApplicationName("AppHarbour tests")
        cls.qt.setStyle("Fusion")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(
            str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat
        )
        self.settings.setValue("appearance/theme", "Light")
        library = Library(
            Path(self.temp.name) / "data",
            Path(self.temp.name) / "apps",
            desktop_dir=Path(self.temp.name) / "desktop",
        )
        self.window = LauncherWindow(self.settings, library)
        self.window.show()
        self.qt.processEvents()
        self.addCleanup(self.window.close)

    def visible_ids(self) -> set[str]:
        return {app_id for app_id, card in self.window.cards.items() if card.isVisible()}

    def test_appharbour_identity_and_icon(self) -> None:
        self.assertEqual(self.window.windowTitle(), "AppHarbour")
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertFalse(self.window.tray.icon().isNull())
        self.assertEqual(self.window.findChild(QLabel, "pageTitle").text(), "AppHarbour")
        image = QImage(str(APP_ICON))
        self.assertTrue(image.hasAlphaChannel())
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        for size in (16, 24, 32, 64):
            self.assertFalse(self.window.windowIcon().pixmap(size, size).isNull())

    def test_entry_point_uses_appharbour_identity(self) -> None:
        from appharbour.app import main

        with (
            patch("appharbour.app.QApplication") as application,
            patch("appharbour.app.LauncherWindow") as window,
            patch("appharbour.app.QTimer.singleShot") as timer,
        ):
            application.return_value.exec.return_value = 0
            self.assertEqual(main(), 0)
            application.return_value.setOrganizationName.assert_called_once_with("AppHarbour")
            application.return_value.setApplicationName.assert_called_once_with("AppHarbour")
            application.return_value.setApplicationDisplayName.assert_called_once_with("AppHarbour")
            application.return_value.setDesktopFileName.assert_called_once_with("appharbour")
            window.return_value.library.create_launcher_shortcut.assert_called_once()
            timer.assert_called_once_with(0, window.return_value.refresh_releases)

    def test_close_hides_to_tray_and_activation_restores_window(self) -> None:
        with patch("appharbour.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
            self.window.tray.show()
            self.assertFalse(self.window.close())
            self.assertFalse(self.window.isVisible())
            self.assertTrue(self.window.tray.isVisible())
            self.window.tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
            self.assertFalse(self.window.isVisible())
            self.window.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
            self.assertTrue(self.window.isVisible())
            self.window.showMinimized()
            self.window.tray_menu.actions()[0].trigger()
            self.assertFalse(self.window.isMinimized())
            self.window.quit_launcher()
            self.assertFalse(self.window.isVisible())
            self.assertFalse(self.window.tray.isVisible())

    def test_no_tray_host_keeps_normal_close_behaviour(self) -> None:
        with patch("appharbour.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
            self.window.tray.show()
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertFalse(self.window.tray.isVisible())

    def test_tray_menu_launches_only_managed_apps_and_shows_update_count(self) -> None:
        self.assertFalse(self.window.tray_apps_menu.isEnabled())
        self.assertTrue(self.window.tray_check_action.isEnabled())
        payload = appimage_bytes()
        with patch("appharbour.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        self.window.releases[APP.id] = release_for("1.1.0", payload)
        self.window.scan_installed()
        self.assertEqual(self.window.tray.toolTip(), "AppHarbour · Updates: 1")
        actions = self.window.tray_apps_menu.actions()
        self.assertEqual([action.text() for action in actions], [APP.name])
        with patch.object(self.window, "launch_app") as launch:
            actions[0].trigger()
            launch.assert_called_once_with(APP.id)
        self.assertTrue(self.window.tray_check_action.isEnabled())

    def test_hiding_keeps_download_running_and_quit_can_cancel_safely(self) -> None:
        with (
            patch(
                "appharbour.window.QSystemTrayIcon.isSystemTrayAvailable",
                return_value=True,
            ),
            patch("appharbour.window.QThreadPool.globalInstance"),
            patch("appharbour.window.QMessageBox.question") as question,
        ):
            self.window.tray.show()
            self.window.start_task("Downloading", lambda cancel, progress: TaskResult("Done"))
            task = self.window.task
            self.assertTrue(self.window.tray_check_action.isEnabled())
            self.window.close()
            self.assertFalse(task.cancel.is_set())
            question.assert_not_called()
            question.return_value = QMessageBox.StandardButton.No
            self.window.quit_launcher()
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.window.quitting)
            self.assertFalse(task.cancel.is_set())
            question.return_value = QMessageBox.StandardButton.Yes
            self.window.quit_launcher()
            self.assertTrue(task.cancel.is_set())
            self.assertTrue(self.window.close_after_task)
            self.assertTrue(self.window.quitting)
            self.window.task_finished(TaskResult("Cancelled"))
            self.assertIsNone(self.window.task)
            self.assertFalse(self.window.tray.isVisible())

    def test_catalog_icons_and_install_actions(self) -> None:
        self.assertEqual(len(APPS), 14)
        self.assertEqual(len({app.id for app in APPS}), 14)
        self.assertEqual(len(self.visible_ids()), 14)
        for app in APPS:
            with self.subTest(app=app.id):
                self.assertEqual(self.window.cards[app.id].findChildren(QComboBox), [])
                self.assertTrue(self.window.cards[app.id].preferences_action.isEnabled())
                self.assertFalse(QPixmap(str(app.icon)).isNull())
                install = next(
                    button
                    for button in self.window.cards[app.id].findChildren(QPushButton)
                    if button.text() == "Install"
                )
                self.assertTrue(install.isEnabled())
                self.assertIn("verify", install.toolTip())
                self.assertFalse(self.window.cards[app.id].shortcut_action.isEnabled())
                self.assertFalse(hasattr(self.window.cards[app.id], "link_action"))

    def test_search_by_name_description_repository_and_clear(self) -> None:
        self.window.search.setText("  SOLIDcraft  ")
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        self.window.search.setText("CAD")
        self.assertEqual(self.visible_ids(), {"cadcraft", "solidcraft"})
        self.window.search.setText("tangency-labs")
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        self.window.search.setText("no matching app")
        self.assertFalse(self.visible_ids())
        self.assertTrue(self.window.empty.isVisible())
        self.assertIn("No apps match", self.window.empty.text())
        self.window.search.clear()
        self.assertEqual(len(self.visible_ids()), 14)
        self.assertFalse(self.window.empty.isVisible())

    def test_empty_filters_and_return_to_library(self) -> None:
        self.assertEqual(self.window.tabs.tabText(0), "All apps")
        self.assertEqual(self.window.tabs.tabText(1), "Installed (0)")
        for index in (1, 2):
            self.window.tabs.setCurrentIndex(index)
            self.assertFalse(self.visible_ids())
            self.assertTrue(self.window.empty.isVisible())
            self.assertFalse(self.window.scroll_area.isVisible())
        self.window.tabs.setCurrentIndex(0)
        self.assertEqual(len(self.visible_ids()), 14)

    def test_update_count_and_versions_are_visible_before_selecting_updates(self) -> None:
        path = Path(self.temp.name) / "solidcraft-0.16.1-linux-x86_64.AppImage"
        self.window.installed["solidcraft"] = Installed(path, "0.16.1", True)
        release = Release("0.16.2", "solidcraft.AppImage", "unused", 1, "0" * 64)
        self.window.releases["solidcraft"] = release
        self.window.refresh()

        self.assertEqual(self.window.tabs.currentIndex(), 0)
        self.assertEqual(self.window.tabs.tabText(2), "Updates (1)")
        card = self.window.cards["solidcraft"]
        self.assertIn("v0.16.1", card.status.text())
        self.assertIn("v0.16.2", card.available_version.text.text())
        self.assertEqual(card.action_button.text(), "Launch")
        self.window.tabs.setCurrentIndex(2)
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        self.assertIn("v0.16.1", card.status.text())
        self.assertIn("v0.16.2", card.available_version.text.text())

        self.window.search.setText("photocraft")
        self.assertFalse(self.visible_ids())
        self.assertEqual(self.window.tabs.tabText(0), "All apps")
        self.assertEqual(self.window.tabs.tabText(1), "Installed (1)")
        self.assertEqual(self.window.tabs.tabText(2), "Updates (1)")
        self.window.installed["solidcraft"] = Installed(path, "0.16.2", True)
        self.window.refresh()
        self.assertEqual(self.window.tabs.tabText(2), "Updates (0)")

    def test_update_starts_in_launcher_and_keeps_launch_available(self) -> None:
        app = next(app for app in APPS if app.id == "solidcraft")
        path = Path(self.temp.name) / "solidcraft-0.16.1-linux-x86_64.AppImage"
        self.window.installed[app.id] = Installed(path, "0.16.1", True)
        self.window.releases[app.id] = Release("0.16.2", "unused", "unused", 1, "0" * 64)
        self.window.refresh()
        card = self.window.cards[app.id]
        self.assertTrue(card.update_button.isVisible())
        self.assertEqual(card.update_button.text(), "Update")
        self.assertEqual(card.action_button.text(), "Launch")
        with (
            patch.object(self.window, "start_task") as start,
            patch("appharbour.window.QDesktopServices.openUrl") as open_url,
        ):
            QTest.mouseClick(card.update_button, Qt.MouseButton.LeftButton)
        start.assert_called_once()
        open_url.assert_not_called()

    def test_grid_reflows_and_preserves_cards_when_filtering(self) -> None:
        self.window.resize(1400, 800)
        self.qt.processEvents()
        wide_columns = self.window.grid.columns
        first_card = self.window.cards["photocraft"]
        self.window.resize(760, 600)
        self.qt.processEvents()
        self.assertLess(self.window.grid.columns, wide_columns)
        self.assertEqual(self.window.grid.layout_grid.count(), 14)
        viewport = self.window.scroll_area.viewport()
        self.assertLessEqual(self.window.grid.width(), viewport.width())
        cards = self.window.grid.cards
        for index, card in enumerate(cards):
            self.assertGreaterEqual(card.x(), 0)
            self.assertLessEqual(card.geometry().right(), self.window.grid.width())
            for other in cards[index + 1 :]:
                self.assertFalse(card.geometry().intersects(other.geometry()))
        self.window.search.setText("PhotoCraft")
        self.assertIs(self.window.grid.cards[0], first_card)

    def test_compact_cards_keep_versions_and_actions_inline_at_narrow_widths(self) -> None:
        info = self.release_info()
        for width in (760, 1120):
            self.window.resize(width, 1000)
            self.qt.processEvents()
            for installed in (
                None,
                Installed(Path(self.temp.name) / "old.AppImage", "0.9.0", True),
                Installed(
                    Path(self.temp.name) / "preview.AppImage",
                    info.preview.version,
                    True,
                    prerelease=True,
                ),
            ):
                card = self.window.cards[APP.id]
                offered = info.stable.release if installed else info.preview.release
                card.set_state(installed, offered, False, info=info)
                self.qt.processEvents()
                version = card.installed_version if installed else card.available_version
                caption = version.caption.mapTo(card, version.caption.rect().center())
                button = card.action_button.geometry()
                self.assertLessEqual(button.top(), caption.y())
                self.assertGreaterEqual(button.bottom(), caption.y())
                text = version.text.mapTo(card, version.text.rect().topRight())
                badge = version.badge.mapTo(card, version.badge.rect().bottomRight())
                self.assertLess(text.x(), button.left())
                self.assertLess(badge.x(), button.left())
                self.assertLess(card.height(), 280)
                self.assertEqual(card.action_button.text(), "Launch" if installed else "Install")
            self.window.refresh()
            self.qt.processEvents()
            self.assertLess(max(card.height() for card in self.window.grid.cards), 280)

    def test_clicking_name_opens_real_details_dialog(self) -> None:
        observed = []

        def close_details() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, DetailsDialog):
                observed.append(dialog.windowTitle())
                observed.extend(label.text() for label in dialog.findChildren(QLabel))
                dialog.reject()

        QTimer.singleShot(0, close_details)
        name = self.window.cards["solidcraft"].findChild(QPushButton, "appName")
        self.assertIsNotNone(name)
        QTest.mouseClick(name, Qt.MouseButton.LeftButton)
        self.assertIn("About SolidCraft", observed)
        self.assertIn("tangency-labs/solidcraft", observed)

    def test_settings_save_cancel_and_theme_persistence(self) -> None:
        folder = str(Path(self.temp.name) / "future-apps")

        def save_settings() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                dialog.theme.setCurrentText("Dark")
                dialog.folder.setText(folder)
                dialog.accept()

        QTimer.singleShot(0, save_settings)
        self.window.open_settings()
        self.assertEqual(self.settings.value("appearance/theme"), "Dark")
        self.assertEqual(self.settings.value("installation/folder"), folder)
        self.assertFalse(Path(folder).exists())
        self.assertEqual(self.qt.palette().color(QPalette.ColorRole.Window).name(), "#171b24")
        self.settings.sync()
        reopened = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(reopened.value("appearance/theme"), "Dark")

        def cancel_settings() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                dialog.theme.setCurrentText("Light")
                dialog.reject()

        QTimer.singleShot(0, cancel_settings)
        self.window.open_settings()
        self.assertEqual(self.settings.value("appearance/theme"), "Dark")

    def wait_for_task(self) -> None:
        deadline = time.monotonic() + 5
        while self.window.task is not None and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(0.001)  # Let Python worker threads run between Qt waits.
        self.assertIsNone(self.window.task, "Background operation did not finish")

    def test_new_folder_name_is_readable_in_light_and_dark_themes(self) -> None:
        for theme in ("Light", "Dark"):
            with self.subTest(theme=theme):
                self.settings.setValue("appearance/theme", theme)
                self.window.apply_theme()
                dialog = SettingsDialog(self.settings, self.window)
                self.addCleanup(dialog.close)
                dialog.folder.setText(self.temp.name)
                observations = []

                def type_folder_name() -> None:
                    picker = self.qt.activeModalWidget()
                    if not isinstance(picker, QFileDialog):
                        if picker is not None:
                            picker.close()
                        return
                    try:
                        create = picker.findChild(QToolButton, "newFolderButton")
                        if create is None:
                            return
                        create.click()
                        QTest.qWait(50)
                        editor = next(
                            (
                                edit
                                for edit in picker.findChildren(QLineEdit)
                                if edit.isVisible() and not edit.objectName()
                            ),
                            None,
                        )
                        if editor is None:
                            return
                        editor.selectAll()
                        QTest.keyClicks(editor, "My AppImages")
                        option = QStyleOptionFrame()
                        editor.initStyleOption(option)
                        text_rect = editor.style().subElementRect(
                            QStyle.SubElement.SE_LineEditContents, option, editor
                        )
                        observations.append(
                            (editor.text(), text_rect.height(), editor.fontMetrics().height())
                        )
                    finally:
                        picker.reject()

                QTimer.singleShot(100, type_folder_name)
                dialog.browse()
                self.assertEqual(len(observations), 1, "New-folder editor did not appear")
                text, available_height, font_height = observations[0]
                self.assertEqual(text, "My AppImages")
                self.assertGreaterEqual(available_height, font_height)
                self.assertEqual(dialog.folder.text(), self.temp.name)

    def wait_for_release_checks(self) -> None:
        deadline = time.monotonic() + 10
        while (
            self.window.release_task or self.window.release_queue
        ) and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(0.001)
        self.assertIsNone(self.window.release_task, "Background release check did not finish")
        self.assertFalse(self.window.release_queue)

    def release_info(self) -> ReleaseInfo:
        payload = appimage_bytes()
        stable = release_for("1.0.0", payload)
        preview = replace(release_for("1.1.0-beta.1", payload), prerelease=True, is_latest=False)
        return ReleaseInfo(
            ReleaseOption(stable.version, stable),
            ReleaseOption(preview.version, preview),
            time.time(),
        )

    def test_refresh_checks_uninstalled_cards_and_offers_no_preview_update_by_default(self) -> None:
        info = self.release_info()
        with patch("appharbour.window.discover_releases", return_value=info) as discover:
            self.window.refresh_releases()
            self.assertTrue(self.window.cards[APP.id].action_button.isEnabled())
            self.wait_for_release_checks()
            self.assertEqual(discover.call_count, len(APPS))
            self.assertEqual(
                {call.args[0].id for call in discover.call_args_list}, {app.id for app in APPS}
            )
            self.assertTrue(all(not call.kwargs["force"] for call in discover.call_args_list))
            self.window.check_button.click()
            self.wait_for_release_checks()
            self.assertTrue(
                all(call.kwargs["force"] for call in discover.call_args_list[len(APPS) :])
            )
        card = self.window.cards[APP.id]
        self.assertEqual(card.available_version.badge.text(), "Latest")
        self.assertEqual(card.other_version.badge.text(), "Pre-release")
        self.assertEqual(card.action_button.text(), "Install")
        self.assertEqual(self.window.tabs.tabText(2), "Updates (0)")
        self.assertFalse(hasattr(self.window, "rescan_button"))
        self.assertEqual(self.window.check_button.text(), "Refresh releases")

    def change_release_preference(self, index: int) -> None:
        selected = []

        def choose() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, RepositoriesDialog):
                try:
                    selected.append(dialog.selected_app.id)
                    dialog.release_channel.setCurrentIndex(index)
                finally:
                    dialog.reject()

        QTimer.singleShot(0, choose)
        self.window.cards[APP.id].preferences_action.trigger()
        self.assertEqual(selected, [APP.id])

    def test_card_preference_changes_offers_and_count_without_installing(self) -> None:
        info = self.release_info()
        payload = appimage_bytes()
        with patch("appharbour.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(APP, info.stable.release, Event(), lambda *args: None)
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        self.assertEqual(self.window.tabs.tabText(2), "Updates (0)")
        with patch.object(self.window.library, "install") as install:
            self.change_release_preference(1)
            install.assert_not_called()
        card = self.window.cards[APP.id]
        self.assertEqual(self.window.tabs.tabText(2), "Updates (1)")
        self.assertEqual(card.available_version.badge.text(), "Pre-release")
        self.assertTrue(card.other_version.isHidden())
        self.assertEqual(card.installed_version.badge.text(), "Latest")
        self.assertTrue(card.app.include_prereleases)
        reopened = LauncherWindow(self.settings, self.window.library)
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.cards[APP.id].app.include_prereleases)
        self.assertEqual(reopened.cards[APP.id].findChildren(QComboBox), [])
        self.assertEqual(reopened.tabs.tabText(2), "Updates (1)")
        self.change_release_preference(0)
        self.assertEqual(self.window.tabs.tabText(2), "Updates (0)")

    def test_failed_refresh_retains_badges_and_shows_last_checked_without_dialog(self) -> None:
        info = replace(self.release_info(), checked_at=time.time() - 3600)
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        self.assertIn("refresh needed", self.window.cards[APP.id].checked.text())
        with (
            patch("appharbour.window.discover_releases", side_effect=OSError("Offline")),
            patch("appharbour.window.QMessageBox.warning") as warning,
        ):
            self.window.refresh_releases()
            self.wait_for_release_checks()
            warning.assert_not_called()
        card = self.window.cards[APP.id]
        self.assertEqual(card.available_version.badge.text(), "Latest")
        self.assertEqual(card.other_version.badge.text(), "Pre-release")
        self.assertIn("Refresh failed · checked", card.checked.text())
        self.assertIn("Offline", card.checked.toolTip())

    def test_unusable_preview_disables_install_after_opt_in(self) -> None:
        info = replace(
            self.release_info(),
            preview=ReleaseOption("1.1.0-beta.1", None, "No supported AppImage was found."),
        )
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        self.window.scan_installed()
        self.assertTrue(self.window.cards[APP.id].action_button.isEnabled())
        self.change_release_preference(1)
        card = self.window.cards[APP.id]
        self.assertFalse(card.action_button.isEnabled())
        self.assertIn("No supported AppImage", card.action_button.toolTip())
        self.assertEqual(self.window.tabs.tabText(2), "Updates (0)")

    def test_closing_cancels_background_refresh_without_installation_prompt(self) -> None:
        with (
            patch("appharbour.window.QThreadPool.globalInstance"),
            patch("appharbour.window.QMessageBox.question") as question,
            patch("appharbour.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False),
        ):
            self.window.refresh_releases()
            task = self.window.release_task
            self.assertTrue(self.window.cards[APP.id].preferences_action.isEnabled())
            self.assertFalse(self.window.close())
            self.assertTrue(task.cancel.is_set())
            self.assertFalse(self.window.release_queue)
            self.window.release_check_finished(TaskResult("Cancelled"))
            question.assert_not_called()
            self.assertIsNone(self.window.release_task)
            self.assertFalse(self.window.isVisible())

    def test_managed_appimage_gets_launch_action_and_installed_filter(self) -> None:
        payload = appimage_bytes()
        with patch("appharbour.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        self.window.scan_installed()
        path = self.window.installed[APP.id].path
        card = self.window.cards[APP.id]
        self.assertEqual(card.action_button.text(), "Launch")
        self.window.tabs.setCurrentIndex(1)
        self.assertEqual(self.visible_ids(), {APP.id})
        with patch("appharbour.library.subprocess.Popen") as spawn:
            spawn.return_value.poll.return_value = None
            QTest.mouseClick(card.action_button, Qt.MouseButton.LeftButton)
            self.assertEqual(spawn.call_args.args[0], [str(path)])
        self.assertTrue(card.remove_action.isEnabled())
        self.assertTrue(card.shortcut_action.isEnabled())
        shortcut = self.window.library.desktop_dir / f"appharbour-{APP.id}.desktop"
        shortcut.unlink()
        with patch("appharbour.window.QMessageBox.warning") as warning:
            card.shortcut_requested.emit(APP.id)
            warning.assert_not_called()
        self.assertTrue(shortcut.is_file())
        self.assertIn("Name=PhotoCraft (AppHarbour)", shortcut.read_text())

    def test_legacy_external_link_does_not_appear_as_installed_or_updatable(self) -> None:
        path = Path(self.temp.name) / "solidcraft.AppImage"
        path.write_bytes(appimage_bytes())
        self.window.library._save({"solidcraft": Installed(path, "0.16.1", False)})
        self.window.releases["solidcraft"] = Release("0.16.2", "unused", "unused", 1, "0" * 64)
        self.window.scan_installed()
        self.assertNotIn("solidcraft", self.window.installed)
        self.assertEqual(self.window.cards["solidcraft"].action_button.text(), "Install")
        self.assertEqual(self.window.tabs.tabText(2), "Updates (0)")
        self.window.tabs.setCurrentIndex(1)
        self.assertFalse(self.visible_ids())
        self.assertEqual(path.read_bytes(), appimage_bytes())

    def test_install_update_and_rollback_through_background_ui(self) -> None:
        card = self.window.cards[APP.id]
        with patch("appharbour.window.QMessageBox.warning") as warning:
            for version in ("1.0.0", "1.1.0"):
                payload = appimage_bytes(version.encode())
                release = release_for(version, payload)
                self.window.releases[APP.id] = release
                self.window.refresh()
                with (
                    patch("appharbour.window.latest_release", return_value=release),
                    patch("appharbour.releases.request", return_value=io.BytesIO(payload)),
                ):
                    button = card.update_button if version == "1.1.0" else card.action_button
                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                    self.wait_for_task()
                self.assertEqual(self.window.installed[APP.id].version, version)
                self.assertEqual(card.action_button.text(), "Launch")
                self.assertTrue(self.window.installed[APP.id].managed)
            self.assertTrue(card.rollback_action.isEnabled())
            card.rollback_requested.emit(APP.id)
            self.wait_for_task()
            self.assertEqual(self.window.installed[APP.id].version, "1.0.0")
            self.assertTrue(card.update_button.isVisible())
            warning.assert_not_called()

    def test_busy_state_cancellation_and_launch_stays_available(self) -> None:
        payload = appimage_bytes()
        with patch("appharbour.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(
                APP, release_for("1.0.0", payload), Event(), lambda *args: None
            )
        self.window.scan_installed()
        with patch("appharbour.window.QThreadPool.globalInstance"):
            self.window.start_task("Test operation", lambda cancel, progress: TaskResult("Done"))
        task = self.window.task
        self.assertIsNotNone(task)
        self.assertTrue(self.window.cards[APP.id].action_button.isEnabled())
        self.assertFalse(self.window.cards["solidcraft"].action_button.isEnabled())
        self.window.task_progress("Large download", 3 * 1024**3, 4 * 1024**3)
        self.assertEqual(self.window.progress_bar.value(), 75)
        QTest.mouseClick(self.window.cancel_button, Qt.MouseButton.LeftButton)
        self.assertTrue(task.cancel.is_set())
        self.window.task_finished(TaskResult("Cancelled"))
        self.assertTrue(self.window.cards["solidcraft"].action_button.isEnabled())

    def test_settings_reject_invalid_destination(self) -> None:
        dialog = SettingsDialog(self.settings, self.window)
        self.addCleanup(dialog.close)
        for path in ("", "relative/path", "/bad\npath", self.settings.fileName()):
            self.settings.sync()
            dialog.folder.setText(path)
            with (
                self.subTest(path=path),
                patch("appharbour.window.QMessageBox.warning") as warning,
            ):
                dialog.accept()
                warning.assert_called_once()
                self.assertEqual(dialog.result(), 0)

    def test_header_version_opens_about_and_settings_sections_are_accessible(self) -> None:
        observations = []

        def inspect_about() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                observations.append(dialog.sections.currentItem().text())
                for section in range(4):
                    dialog.sections.setCurrentRow(section)
                    self.assertEqual(dialog.pages.currentIndex(), section)
                dialog.reject()

        self.assertEqual(self.window.version_button.text(), f"v{APP_VERSION}")
        QTimer.singleShot(0, inspect_about)
        self.window.version_button.click()
        self.assertEqual(observations, ["About"])

    def test_release_badges_describe_the_version_and_beta_waits_for_stable(self) -> None:
        card = self.window.cards["solidcraft"]
        beta = Installed(
            Path(self.temp.name) / "beta.AppImage", "1.2.0-beta.1", True, prerelease=True
        )
        stable = Release("1.2.0", "solid.AppImage", "unused", 1, "0" * 64)
        card.set_state(beta, stable, False)
        self.assertEqual(card.installed_version.badge.text(), "Pre-release")
        self.assertEqual(card.available_version.badge.text(), "Latest")
        self.assertTrue(card.update_button.isVisible())
        previous = Release("1.1.0", "solid.AppImage", "unused", 1, "0" * 64)
        card.set_state(beta, previous, False)
        self.assertFalse(card.update_button.isVisible())
        self.assertIn("Waiting for a newer stable", card.checked.text())
        self.assertEqual(card.action_button.text(), "Launch")
        current = Installed(beta.path, "1.1.0", True)
        card.set_state(current, previous, False)
        self.assertEqual(card.installed_version.badge.text(), "Latest")
        card.set_state(current, stable, False)
        self.assertEqual(card.installed_version.badge.text(), "")
        self.assertEqual(card.available_version.badge.text(), "Latest")

    def test_preview_only_installation_waits_after_disabling_previews(self) -> None:
        info = self.release_info()
        info = replace(info, stable=None)
        installed = Installed(
            Path(self.temp.name) / "preview.AppImage", info.preview.version, True, prerelease=True
        )
        card = self.window.cards[APP.id]
        card.set_state(installed, None, False, info=info)
        self.assertIn("Waiting for a newer stable", card.checked.text())
        self.assertEqual(card.installed_version.badge.text(), "Pre-release")
        self.assertFalse(card.update_button.isVisible())
        self.assertEqual(card.action_button.text(), "Launch")

    def test_scan_keeps_newer_channel_information_when_catalog_timestamp_is_equal(self) -> None:
        info = self.release_info()
        atomic_json(info_path(APP, self.window.library.data_dir / "release-cache"), asdict(info))
        new_release = release_for("1.2.0", appimage_bytes())
        self.window.release_infos[APP.id] = replace(
            info, stable=ReleaseOption(new_release.version, new_release)
        )
        self.window.scan_installed()
        self.assertEqual(self.window.releases[APP.id], new_release)

    def test_close_to_tray_setting_can_be_disabled(self) -> None:
        def save() -> None:
            dialog = self.qt.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                dialog.close_to_tray.setChecked(False)
                dialog.accept()

        QTimer.singleShot(0, save)
        self.window.open_settings()
        self.assertFalse(self.settings.value("general/close-to-tray", True, type=bool))
        with patch("appharbour.window.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
            self.window.tray.show()
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertTrue(event.isAccepted())


if __name__ == "__main__":
    unittest.main()
