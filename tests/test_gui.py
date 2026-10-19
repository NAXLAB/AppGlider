"""Exercise the actual Qt widgets without network or installation side effects."""

import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QImage, QPalette, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
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

from appharbour import APP_ICON
from appharbour.catalog import APPS
from appharbour.jobs import TaskResult
from appharbour.library import Installed, Library
from appharbour.releases import Release
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
        ):
            application.return_value.exec.return_value = 0
            self.assertEqual(main(), 0)
            application.return_value.setOrganizationName.assert_called_once_with("AppHarbour")
            application.return_value.setApplicationName.assert_called_once_with("AppHarbour")
            application.return_value.setApplicationDisplayName.assert_called_once_with("AppHarbour")
            application.return_value.setDesktopFileName.assert_called_once_with("appharbour")
            window.return_value.library.create_launcher_shortcut.assert_called_once()

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
        self.assertFalse(self.window.tray_check_action.isEnabled())
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
            self.assertFalse(self.window.tray_check_action.isEnabled())
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
        self.assertIn("v0.16.1 → v0.16.2", card.status.text())
        self.assertEqual(card.action_button.text(), "Launch")
        self.window.tabs.setCurrentIndex(2)
        self.assertEqual(self.visible_ids(), {"solidcraft"})
        self.assertIn("v0.16.1 → v0.16.2", card.status.text())

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

    def test_check_updates_with_empty_library_makes_no_requests(self) -> None:
        with patch.object(self.window, "start_task") as start:
            self.window.check_updates()
            start.assert_not_called()
        self.assertIn("Install an app", self.window.footer.text())

    def test_check_updates_only_requests_managed_installed_apps(self) -> None:
        payload = appimage_bytes()
        release = release_for("1.0.0", payload)
        with patch("appharbour.releases.request", return_value=io.BytesIO(payload)):
            self.window.library.install(APP, release, Event(), lambda *args: None)
        with (
            patch("appharbour.window.latest_release", return_value=release) as latest,
            patch("appharbour.window.QMessageBox.warning") as warning,
        ):
            self.window.check_updates()
            self.wait_for_task()
            latest.assert_called_once()
            self.assertEqual(latest.call_args.args[0], APP)
            self.assertEqual(
                latest.call_args.args[2], self.window.library.data_dir / "release-cache"
            )
            warning.assert_not_called()
        self.assertIn("Installed apps checked: 1.", self.window.footer.text())

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


if __name__ == "__main__":
    unittest.main()
