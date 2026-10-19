"""Card library, installed applications and background installation controls."""

from collections.abc import Callable
from pathlib import Path
from threading import Event

from PySide6.QtCore import (
    QEvent,
    QObject,
    QSettings,
    QStandardPaths,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QIcon,
    QPalette,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from appharbour import APP_ICON, APP_NAME
from appharbour.catalog import APPS, App
from appharbour.icons import icon_pixmap
from appharbour.jobs import Task, TaskResult
from appharbour.library import Installed, Library
from appharbour.releases import Progress, Release, check_cancel, latest_release
from appharbour.repositories_dialog import RepositoriesDialog


def icon_label(app: App, size: int, path: Path | None = None) -> QLabel:
    label = QLabel()
    label.setFixedSize(size, size)
    label.setPixmap(icon_pixmap(path or app.icon, size))
    label.setAccessibleName(f"{app.name} icon")
    return label


class AppCard(QFrame):
    details_requested = Signal(str)
    launch_requested = Signal(str)
    install_requested = Signal(str)
    shortcut_requested = Signal(str)
    rollback_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, app: App, icon_path: Path | None = None) -> None:
        super().__init__()
        self.app = app
        self.installed: Installed | None = None
        self.setObjectName("appCard")
        self.setMinimumWidth(250)
        self.setFixedHeight(248)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)
        top = QHBoxLayout()
        self.icon = icon_label(app, 52, icon_path)
        top.addWidget(self.icon)
        top.addStretch()
        self.update_button = QPushButton("Update")
        self.update_button.setAccessibleName(f"Update {app.name}")
        self.update_button.clicked.connect(lambda: self.install_requested.emit(app.id))
        self.update_button.hide()
        top.addWidget(self.update_button, alignment=Qt.AlignmentFlag.AlignTop)
        more = QToolButton()
        more.setText("⋯")
        more.setObjectName("cardMenu")
        more.setAccessibleName(f"More actions for {app.name}")
        more.setToolTip(f"More actions for {app.name}")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction("View details").triggered.connect(
            lambda: self.details_requested.emit(app.id)
        )
        menu.addAction("Open repository").triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(app.source_url))
        )
        menu.addSeparator()
        self.shortcut_action = menu.addAction("Create/repair menu shortcut")
        self.shortcut_action.triggered.connect(lambda: self.shortcut_requested.emit(app.id))
        self.rollback_action = menu.addAction("Roll back to previous version")
        self.rollback_action.triggered.connect(lambda: self.rollback_requested.emit(app.id))
        self.remove_action = menu.addAction("Uninstall…")
        self.remove_action.triggered.connect(lambda: self.remove_requested.emit(app.id))
        more.setMenu(menu)
        top.addWidget(more, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        name = QPushButton(app.name)
        name.setObjectName("appName")
        name.setAccessibleName(f"View {app.name} details")
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.clicked.connect(lambda: self.details_requested.emit(app.id))
        layout.addWidget(name, alignment=Qt.AlignmentFlag.AlignLeft)
        description = QLabel(app.description)
        description.setObjectName("muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addStretch()
        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("status")
        bottom.addWidget(self.status)
        bottom.addStretch()
        self.action_button = QPushButton()
        self.action_button.clicked.connect(self.activate)
        bottom.addWidget(self.action_button)
        layout.addLayout(bottom)

    def activate(self) -> None:
        if self.installed is not None:
            self.launch_requested.emit(self.app.id)
        else:
            self.install_requested.emit(self.app.id)

    def set_state(
        self, installed: Installed | None, release: Release | None, busy: bool, error: str = ""
    ) -> None:
        self.installed = installed
        newer = bool(installed and release and release.newer_than(installed.version))
        self.action_button.setText("Launch" if installed else "Install")
        self.action_button.setAccessibleName(
            f"{'Launch' if installed else 'Install'} {self.app.name}"
        )
        self.action_button.setEnabled(installed is not None or not busy)
        self.rollback_action.setEnabled(
            bool(
                not busy
                and installed
                and installed.managed
                and installed.previous
                and installed.previous.is_file()
            )
        )
        self.remove_action.setEnabled(bool(not busy and installed and installed.managed))
        self.shortcut_action.setEnabled(bool(not busy and installed and installed.managed))
        self.update_button.setVisible(newer)
        self.update_button.setEnabled(not busy)
        if installed:
            version = f" · v{installed.version}" if installed.version else ""
            self.status.setText(
                f"Update available\nv{installed.version} → v{release.version}"
                if newer and release
                else "Installed" + version
            )
            self.status.setToolTip(f"Installed with AppHarbour\n{installed.path}\n{error}".strip())
            self.action_button.setToolTip(f"Launch {installed.path}")
        else:
            self.status.setText(f"Available · v{release.version}" if release else "Not installed")
            self.status.setToolTip(error)
            self.action_button.setToolTip("Download and verify the latest stable AppImage")
        if release:
            self.update_button.setToolTip(
                f"Update to v{release.version}; keep the previous version"
            )


class CardGrid(QWidget):
    """Reflow existing cards without losing keyboard focus."""

    def __init__(self) -> None:
        super().__init__()
        self.cards: list[AppCard] = []
        self.columns = 0
        self.layout_grid = QGridLayout(self)
        self.layout_grid.setContentsMargins(0, 0, 0, 0)
        self.layout_grid.setSpacing(16)
        self.layout_grid.setAlignment(Qt.AlignmentFlag.AlignTop)

    def set_cards(self, cards: list[AppCard], width: int) -> None:
        self.cards = cards
        self.reflow(width)

    def reflow(self, width: int) -> None:
        while self.layout_grid.count():
            self.layout_grid.takeAt(0)
        for column in range(self.columns):
            self.layout_grid.setColumnStretch(column, 0)
        self.columns = max(1, (width + 16) // 286)
        for index, card in enumerate(self.cards):
            self.layout_grid.addWidget(card, index // self.columns, index % self.columns)
        for column in range(self.columns):
            self.layout_grid.setColumnStretch(column, 1)


class DetailsDialog(QDialog):
    def __init__(
        self,
        app: App,
        parent: QWidget,
        installed: Installed | None = None,
        release: Release | None = None,
        error: str = "",
        icon_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {app.name}")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(18)
        heading = QHBoxLayout()
        heading.addWidget(icon_label(app, 64, icon_path))
        title = QLabel(app.name)
        title.setObjectName("pageTitle")
        heading.addWidget(title)
        heading.addStretch()
        layout.addLayout(heading)
        description = QLabel(app.description)
        description.setWordWrap(True)
        layout.addWidget(description)
        info = QFormLayout()
        repo = QLabel(app.repo)
        repo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addRow("Repository", repo)
        info.addRow("Format", QLabel("Linux AppImage"))
        info.addRow(
            "Installed version",
            QLabel(installed.version or "Unknown" if installed else "Not installed"),
        )
        info.addRow("Latest version", QLabel(release.version if release else "Not checked"))
        if installed:
            location = QLabel(str(installed.path))
            location.setWordWrap(True)
            location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info.addRow("Location", location)
        layout.addLayout(info)
        note = QLabel(
            error
            or "Installed apps and their updates are managed by AppHarbour. "
            "Downloads are verified against their published SHA-256."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        source = buttons.addButton("Open repository", QDialogButtonBox.ButtonRole.ActionRole)
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(app.source_url)))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SettingsDialog(QDialog):
    repositories_requested = Signal()

    def __init__(self, settings: QSettings, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.resize(600, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        title = QLabel("Make AppHarbour yours")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        appearance = QGroupBox("Appearance")
        appearance_form = QFormLayout(appearance)
        self.theme = QComboBox()
        self.theme.setAccessibleName("Appearance")
        self.theme.addItems(["System", "Light", "Dark"])
        self.theme.setCurrentText(str(settings.value("appearance/theme", "System")))
        appearance_form.addRow("Colour scheme", self.theme)
        layout.addWidget(appearance)
        downloads = QGroupBox("Downloads")
        download_layout = QVBoxLayout(downloads)
        download_layout.addWidget(QLabel("Install new applications in"))
        default_folder = str(
            Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
            )
            / "appharbour"
            / "apps"
        )
        self.folder = QLineEdit(str(settings.value("installation/folder", default_folder)))
        self.folder.setAccessibleName("AppImage folder")
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        folder_row.addWidget(browse)
        download_layout.addLayout(folder_row)
        note = QLabel("Existing applications stay in their current folders.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        download_layout.addWidget(note)
        layout.addWidget(downloads)
        applications = QGroupBox("Applications")
        application_layout = QHBoxLayout(applications)
        description = QLabel("Manage repositories and application icons.")
        description.setObjectName("muted")
        description.setWordWrap(True)
        application_layout.addWidget(description, stretch=1)
        self.repositories_button = QPushButton("Repositories…")
        self.repositories_button.clicked.connect(self.repositories_requested.emit)
        application_layout.addWidget(self.repositories_button)
        layout.addWidget(applications)
        layout.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        layout.addWidget(buttons)

    def browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose AppImage folder", self.folder.text()
        )
        if folder:
            self.folder.setText(folder)

    def accept(self) -> None:
        if any(ord(c) < 32 or ord(c) == 127 for c in self.folder.text()):
            QMessageBox.warning(self, "Choose a folder", "The path contains control characters.")
            return
        folder = Path(self.folder.text().strip()).expanduser()
        if not self.folder.text().strip() or not folder.is_absolute():
            QMessageBox.warning(self, "Choose a folder", "Enter an absolute path or use Browse.")
            return
        if folder.exists() and not folder.is_dir():
            QMessageBox.warning(self, "Choose a folder", "The selected path is a file.")
            return
        self.folder.setText(str(folder))
        super().accept()


class LauncherWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, library: Library | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else QSettings()
        data_dir = (
            Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
            )
            / "appharbour"
        )
        self.library = (
            library
            if library is not None
            else Library(
                data_dir,
                Path(str(self.settings.value("installation/folder", str(data_dir / "apps")))),
            )
        )
        self.installed: dict[str, Installed] = {}
        self.apps: tuple[App, ...] = APPS
        self.releases: dict[str, Release] = {}
        self.check_errors: dict[str, str] = {}
        self.task: Task | None = None
        self.close_after_task = False
        self.quitting = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.resize(1120, 800)
        self.setMinimumSize(720, 520)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(32, 28, 32, 20)
        layout.setSpacing(20)
        self.setCentralWidget(body)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(56, 56))
        logo.setFixedSize(56, 56)
        logo.setAccessibleName(APP_NAME)
        header.addWidget(logo)
        titles = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("pageTitle")
        titles.addWidget(title)
        subtitle = QLabel("One place for your Linux AppImages.")
        subtitle.setObjectName("muted")
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setAccessibleName("Search apps")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(245)
        header.addWidget(self.search)
        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(self.open_settings)
        header.addWidget(settings_button)
        layout.addLayout(header)
        navigation = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setAccessibleName("Library filters")
        self.tabs.setDrawBase(False)
        for name in ("All apps", "Installed", "Updates"):
            self.tabs.addTab(name)
        navigation.addWidget(self.tabs)
        navigation.addStretch()
        self.rescan_button = QPushButton("Refresh library")
        self.rescan_button.clicked.connect(self.scan_installed)
        navigation.addWidget(self.rescan_button)
        self.update_all_button = QPushButton("Update all")
        self.update_all_button.clicked.connect(self.update_all)
        navigation.addWidget(self.update_all_button)
        self.check_button = QPushButton("Check updates")
        self.check_button.clicked.connect(self.check_updates)
        navigation.addWidget(self.check_button)
        layout.addLayout(navigation)
        self.results = QLabel()
        self.results.setObjectName("muted")
        layout.addWidget(self.results)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid = CardGrid()
        self.scroll_area.setWidget(self.grid)
        layout.addWidget(self.scroll_area, stretch=1)
        self.empty = QLabel()
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty, stretch=1)
        self.footer = QLabel("Loading your library…")
        self.footer.setObjectName("footer")
        self.footer.setWordWrap(True)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.footer, stretch=1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(170)
        self.progress_bar.hide()
        progress_row.addWidget(self.progress_bar)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_task)
        self.cancel_button.hide()
        progress_row.addWidget(self.cancel_button)
        layout.addLayout(progress_row)
        self.cards = {app.id: self.create_card(app) for app in self.apps}
        self.search.textChanged.connect(self.refresh)
        self.tabs.currentChanged.connect(self.refresh)
        QApplication.styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        self.scroll_area.viewport().installEventFilter(self)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction("Open AppHarbour").triggered.connect(self.show_launcher)
        self.tray_check_action = self.tray_menu.addAction("Check updates")
        self.tray_check_action.triggered.connect(self.check_updates)
        self.tray_apps_menu = self.tray_menu.addMenu("Installed apps")
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Quit AppHarbour").triggered.connect(self.quit_launcher)
        self.tray_menu.aboutToShow.connect(self.scan_installed)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.apply_theme()
        self.scan_installed()

    def create_card(self, app: App) -> AppCard:
        card = AppCard(app, self.library.icon_path(app))
        card.setParent(self.grid)
        card.details_requested.connect(self.open_details)
        card.launch_requested.connect(self.launch_app)
        card.install_requested.connect(self.install_app)
        card.shortcut_requested.connect(self.create_shortcut)
        card.rollback_requested.connect(self.rollback_app)
        card.remove_requested.connect(self.remove_app)
        return card

    def show_launcher(self) -> None:
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_launcher()

    def quit_launcher(self) -> None:
        self.show_launcher()
        self.quitting = True
        if not self.close() and not self.close_after_task:
            self.quitting = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.scroll_area.viewport() and isinstance(event, QResizeEvent):
            width = event.size().width()
            if max(1, (width + 16) // 286) != self.grid.columns:
                self.grid.reflow(width)
        return super().eventFilter(watched, event)

    def refresh(self) -> None:
        update_count = sum(self.has_update(app.id) for app in self.apps)
        self.tabs.setTabText(1, f"Installed ({len(self.installed)})")
        self.tabs.setTabText(2, f"Updates ({update_count})")
        self.tray.setToolTip(f"AppHarbour · Updates: {update_count}")
        self.tray_check_action.setEnabled(self.task is None and bool(self.installed))
        self.tray_apps_menu.clear()
        self.tray_apps_menu.setEnabled(bool(self.installed))
        for app in self.apps:
            if app.id in self.installed:
                action = self.tray_apps_menu.addAction(
                    QIcon(icon_pixmap(self.library.icon_path(app), 64)), app.name
                )
                action.triggered.connect(
                    lambda checked=False, app_id=app.id: self.launch_app(app_id)
                )
        query = self.search.text().strip().casefold()
        visible = [
            app
            for app in self.apps
            if (
                self.tabs.currentIndex() == 0
                or (self.tabs.currentIndex() == 1 and app.id in self.installed)
                or (self.tabs.currentIndex() == 2 and self.has_update(app.id))
            )
            and query in f"{app.name} {app.description} {app.repo}".casefold()
        ]
        ids = {app.id for app in visible}
        for app_id, card in self.cards.items():
            card.icon.setPixmap(icon_pixmap(self.library.icon_path(card.app), 52))
            card.set_state(
                self.installed.get(app_id),
                self.releases.get(app_id),
                self.task is not None,
                self.check_errors.get(app_id, ""),
            )
            card.setVisible(app_id in ids)
        self.check_button.setEnabled(self.task is None)
        self.rescan_button.setEnabled(self.task is None)
        self.update_all_button.setVisible(
            any(item.managed and self.has_update(app_id) for app_id, item in self.installed.items())
        )
        self.update_all_button.setEnabled(self.task is None)
        self.grid.set_cards(
            [self.cards[app.id] for app in visible], self.scroll_area.viewport().width()
        )
        self.scroll_area.setVisible(bool(visible))
        self.empty.setVisible(not visible)
        self.results.setText(
            f"{len(visible)} of {len(self.apps)} apps"
            if query
            else f"{len(visible)} apps · AppImage catalog"
        )
        if query:
            self.empty.setText(
                "No apps match your search in this view.\nTry another name or clear the search."
            )
        elif self.tabs.currentIndex() == 1:
            self.empty.setText(
                "Your library is empty.\nChoose Install from All apps to get started."
            )
        elif self.tabs.currentIndex() == 2:
            self.empty.setText("No known updates.\nUse Check updates to look for new releases.")
        else:
            self.empty.setText("No apps match your search.\nTry another name or clear the search.")

    def open_details(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        dialog = DetailsDialog(
            app,
            self,
            self.installed.get(app_id),
            self.releases.get(app_id),
            self.check_errors.get(app_id, ""),
            icon_path=self.library.icon_path(app),
        )
        dialog.exec()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        dialog.repositories_button.setEnabled(self.task is None)
        dialog.repositories_requested.connect(self.open_repositories)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.setValue("appearance/theme", dialog.theme.currentText())
            self.settings.setValue("installation/folder", dialog.folder.text())
            self.library.apps_dir = Path(dialog.folder.text())
            self.apply_theme()
            self.scan_installed()

    def open_repositories(self) -> None:
        if self.task is not None:
            return
        try:
            dialog = RepositoriesDialog(self.library, self)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot open repositories", str(error))
            return
        dialog.catalog_changed.connect(self.scan_installed)
        dialog.exec()

    def has_update(self, app_id: str) -> bool:
        installed, release = self.installed.get(app_id), self.releases.get(app_id)
        return bool(installed and release and release.newer_than(installed.version))

    def scan_installed(self) -> None:
        try:
            apps = self.library.apps
            installed = self.library.scan()
        except (OSError, ValueError) as error:
            self.footer.setText(str(error))
        else:
            if apps != self.apps:
                for app_id in set(self.cards) - {app.id for app in apps}:
                    self.cards.pop(app_id).deleteLater()
                    self.releases.pop(app_id, None)
                    self.check_errors.pop(app_id, None)
                for app in apps:
                    if app.id not in self.cards or self.cards[app.id].app != app:
                        old = self.cards.pop(app.id, None)
                        if old is not None:
                            old.deleteLater()
                        self.cards[app.id] = self.create_card(app)
                self.apps = apps
            self.installed = installed
            if self.task is None:
                self.footer.setText(f"{len(self.installed)} apps installed with AppHarbour.")
        self.refresh()

    def launch_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        installed = self.installed.get(app_id)
        if installed is None:
            self.scan_installed()
            return
        try:
            log = self.library.launch(app, installed)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, f"Cannot launch {app.name}", str(error))
            return
        process = self.library.processes[app_id]
        self.footer.setText(f"Launched {app.name} · Log: {log}")

        def check_startup() -> None:
            code = process.poll()
            if code is not None and code != 0:
                self.footer.setText(f"{app.name} exited with code {code}. See {log}")
                QMessageBox.warning(
                    self,
                    f"{app.name} could not start",
                    f"The application exited with code {code}.\nIts diagnostic log is:\n{log}",
                )

        QTimer.singleShot(1800, check_startup)

    def start_task(self, message: str, operation: Callable[[Event, Progress], TaskResult]) -> None:
        if self.task is not None:
            return
        self.task = Task(operation)
        self.task.signals.progress.connect(self.task_progress)
        self.task.signals.finished.connect(self.task_finished)
        self.task.signals.failed.connect(self.task_failed)
        self.footer.setText(message)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.cancel_button.setText("Cancel")
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.refresh()
        QThreadPool.globalInstance().start(self.task)

    def create_shortcut(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        try:
            self.library.create_shortcut(app)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot create shortcut", str(error))
        else:
            self.footer.setText(f"Created menu shortcut: {app.name} (AppHarbour).")

    def task_progress(self, message: str, received: int, total: int) -> None:
        self.footer.setText(message)
        self.progress_bar.setRange(0, 100 if total > 0 else 0)
        if total > 0:
            self.progress_bar.setValue(min(100, int(received * 100 / total)))

    def reset_task(self) -> None:
        self.task = None
        self.progress_bar.hide()
        self.cancel_button.hide()
        self.scan_installed()

    def task_finished(self, result: TaskResult) -> None:
        self.releases.update(result.releases)
        self.check_errors.update(result.errors)
        for app_id in result.releases:
            self.check_errors.pop(app_id, None)
        self.reset_task()
        self.footer.setText(result.message)
        if result.errors and not self.close_after_task:
            errors = "\n\n".join(f"{key}: {value}" for key, value in result.errors.items())
            QMessageBox.warning(self, "Some operations could not finish", errors)
        if self.close_after_task:
            self.close()

    def task_failed(self, error: str) -> None:
        self.reset_task()
        self.footer.setText(error)
        if self.close_after_task:
            self.close()
        else:
            QMessageBox.warning(self, "Operation could not finish", error)

    def cancel_task(self) -> None:
        if self.task is not None:
            self.task.cancel.set()
            self.cancel_button.setText("Cancelling…")
            self.cancel_button.setEnabled(False)

    def check_updates(self) -> None:
        self.scan_installed()
        apps = [app for app in self.apps if app.id in self.installed]
        if not apps:
            self.footer.setText("Install an app from All apps before checking for updates.")
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            result = TaskResult("Release checks finished.")
            for index, app in enumerate(apps):
                check_cancel(cancel)
                progress(f"Checking {app.name}…", index, len(apps))
                try:
                    result.releases[app.id] = latest_release(
                        app, cancel, self.library.data_dir / "release-cache"
                    )
                except (OSError, ValueError) as error:
                    result.errors[app.id] = str(error)
            result.message = f"Installed apps checked: {len(result.releases)}. " + (
                f"{len(result.errors)} checks could not finish."
                if result.errors
                else "Recent results may be reused for up to 5 minutes."
            )
            return result

        self.start_task("Checking releases…", operation)

    def install_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        installed = self.installed.get(app_id)

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            progress(f"Checking {app.name} release…", 0, 0)
            release = latest_release(app, cancel, self.library.data_dir / "release-cache")
            if installed and not release.newer_than(installed.version):
                return TaskResult(f"{app.name} is already up to date.", {app_id: release})
            self.library.install(
                app,
                release,
                cancel,
                lambda message, done, total: progress(f"{app.name} · {message}", done, total),
            )
            return TaskResult(
                f"{app.name} v{release.version} is ready to launch.", {app_id: release}
            )

        self.start_task(f"Preparing {app.name}…", operation)

    def update_all(self) -> None:
        apps = [
            app for app in self.apps if self.has_update(app.id) and self.installed[app.id].managed
        ]

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            result = TaskResult("Updates finished.")
            for app in apps:
                check_cancel(cancel)
                progress(f"Checking {app.name}…", 0, 0)
                try:
                    release = latest_release(app, cancel, self.library.data_dir / "release-cache")
                    self.library.install(
                        app,
                        release,
                        cancel,
                        lambda message, done, total: progress(
                            f"{app.name} · {message}", done, total
                        ),
                    )
                    result.releases[app.id] = release
                except (OSError, ValueError) as error:
                    result.errors[app.id] = str(error)
            result.message = f"Updated {len(result.releases)} apps."
            return result

        if apps:
            self.start_task("Updating installed apps…", operation)

    def rollback_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            check_cancel(cancel)
            self.library.rollback(app)
            return TaskResult(f"Restored the previous {app.name} version.")

        self.start_task(f"Restoring {app.name}…", operation)

    def remove_app(self, app_id: str) -> None:
        app = next(app for app in self.apps if app.id == app_id)
        answer = QMessageBox.question(
            self,
            f"Remove {app.name}?",
            "Remove this app and its previous version from AppHarbour?\n"
            "Your documents and application settings will be kept.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            check_cancel(cancel)
            self.library.remove(app)
            return TaskResult(f"Removed the managed {app.name} installation.")

        self.start_task(f"Removing {app.name}…", operation)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.quitting and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
            return
        if self.task is not None:
            answer = QMessageBox.question(
                self,
                "Operation in progress",
                "Cancel the current operation and close when it has stopped?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.quitting = True
                self.close_after_task = True
                self.cancel_task()
            event.ignore()
            return
        self.tray.hide()
        super().closeEvent(event)

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        theme = str(self.settings.value("appearance/theme", "System"))
        dark = theme == "Dark" or (
            theme == "System" and app.styleHints().colorScheme() == Qt.ColorScheme.Dark
        )
        background, surface, foreground, muted, border, accent = (
            ("#171b24", "#212734", "#eff3fb", "#abb7cc", "#394458", "#91b6ff")
            if dark
            else ("#f5f6fa", "#ffffff", "#202a3b", "#57647a", "#d5dce8", "#245dc2")
        )
        palette = QPalette()
        for role, color in (
            (QPalette.ColorRole.Window, background),
            (QPalette.ColorRole.WindowText, foreground),
            (QPalette.ColorRole.Base, surface),
            (QPalette.ColorRole.AlternateBase, background),
            (QPalette.ColorRole.Text, foreground),
            (QPalette.ColorRole.Button, surface),
            (QPalette.ColorRole.ButtonText, foreground),
            (QPalette.ColorRole.ToolTipBase, surface),
            (QPalette.ColorRole.ToolTipText, foreground),
            (QPalette.ColorRole.Highlight, accent),
            (QPalette.ColorRole.HighlightedText, background),
            (QPalette.ColorRole.PlaceholderText, muted),
        ):
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(muted))
        app.setPalette(palette)
        app.setStyleSheet(f"""
            QWidget {{ font-size: 14px; }}
            QMainWindow, QDialog {{ background: {background}; }}
            QLabel {{ background: transparent; }}
            QLabel#pageTitle {{ font-size: 27px; font-weight: 650; }}
            QLabel#dialogTitle {{ font-size: 22px; font-weight: 650; }}
            QLabel#sectionTitle {{ font-size: 18px; font-weight: 650; }}
            QGroupBox {{ background: {surface}; border: 1px solid {border};
                border-radius: 10px; margin-top: 12px; padding: 18px 14px 14px; }}
            QDialog#repositoriesDialog QGroupBox {{ padding: 10px 14px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 14px;
                padding: 0 6px; color: {muted}; }}
            QListWidget#repositories {{ background: {surface}; border: 1px solid {border};
                border-radius: 10px; outline: 0; }}
            QListWidget#repositories:focus {{ border: 2px solid {accent}; }}
            QListWidget#repositories::item {{ padding: 12px 8px; border-radius: 6px; }}
            QListWidget#repositories::item:selected {{ background: {background}; color: {accent}; }}
            QLabel#muted, QLabel#footer {{ color: {muted}; }}
            QLabel#footer {{ font-size: 12px; padding-top: 8px; }}
            QLabel#emptyState {{ font-size: 17px; color: {muted}; }}
            QFrame#appCard {{ background: {surface}; border: 1px solid {border};
                border-radius: 12px; }}
            QFrame#appCard:hover {{ border-color: {accent}; }}
            QPushButton, QToolButton {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 9px 14px; }}
            QPushButton:hover, QToolButton:hover {{ border-color: {accent}; color: {accent}; }}
            QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus {{
                border: 2px solid {accent}; }}
            QPushButton:disabled {{ color: {muted}; background: {background}; }}
            QPushButton#primary {{ background: {accent}; color: {background};
                border-color: {accent}; font-weight: 600; }}
            QPushButton#primary:hover {{ border-color: {foreground}; }}
            QPushButton#primary:disabled {{ background: {surface}; color: {muted};
                border-color: {border}; }}
            QPushButton#danger {{ color: {"#ff9d9d" if dark else "#a52626"}; }}
            QPushButton#appName {{ border: none; padding: 0; text-align: left;
                font-size: 19px; font-weight: 650; background: transparent; }}
            QPushButton#appName:focus {{ outline: 2px solid {accent}; }}
            QToolButton#cardMenu {{ border: none; background: transparent;
                font-size: 24px; padding: 0px 6px; }}
            QToolButton::menu-indicator {{ image: none; }}
            QLabel#status {{ color: {muted}; font-size: 12px; }}
            QLineEdit, QComboBox {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 9px; }}
            QAbstractItemView QLineEdit, QAbstractItemView QLineEdit:focus {{
                padding: 0; border: 1px solid {accent}; border-radius: 2px; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: {background}; }}
            QTabBar::tab {{ color: {muted}; padding: 12px 16px;
                border-bottom: 2px solid transparent; }}
            QTabBar::tab:selected {{ color: {accent}; border-bottom-color: {accent}; }}
            QTabBar::tab:focus {{ border: 1px solid {accent}; }}
            QMenu {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; padding: 6px; }}
            QMenu::item {{ padding: 8px 16px; }}
            QMenu::item:selected {{ background: {background}; color: {accent}; }}
        """)
