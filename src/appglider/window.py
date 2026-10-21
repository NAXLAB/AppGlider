"""Card library, installed applications and background installation controls."""

from collections.abc import Callable
from datetime import datetime
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
    QCheckBox,
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
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QSystemTrayIcon,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from appglider import APP_ICON, APP_NAME, APP_VERSION, github_status
from appglider.catalog import APPS, App
from appglider.icons import icon_pixmap
from appglider.jobs import Task, TaskResult
from appglider.library import Installed, Library, Orphan
from appglider.release_checks import ReleaseChecks
from appglider.release_info import (
    ReleaseInfo,
    cached_info,
    discover_releases,
    info_path,
    remember_release,
)
from appglider.releases import (
    BuildChoiceRequired,
    Progress,
    Release,
    cached_release,
    check_cancel,
    file_stamp,
    github_request_status,
    latest_release,
    release_cache_path,
    restore_rate_limits,
    variant_label,
    version_key,
)
from appglider.repositories_dialog import RepositoriesDialog
from appglider.version_label import VersionLabel

CARD_MIN_WIDTH = 300


def icon_label(app: App, size: int, path: Path | None = None) -> QLabel:
    label = QLabel()
    label.setFixedSize(size, size)
    label.setPixmap(icon_pixmap(path or app.icon, size))
    label.setAccessibleName(f"{app.name} icon")
    return label


# Appearance -> background, surface, text, muted text, border and accent colours. "System"
# instead follows the desktop's own palette.
THEMES: dict[str, tuple[str, ...]] = {
    "Light": ("#f5f6fa", "#ffffff", "#202a3b", "#57647a", "#d5dce8", "#245dc2"),
    "Dark": ("#171b24", "#212734", "#eff3fb", "#abb7cc", "#394458", "#91b6ff"),
    "Forest": ("#1c1f1a", "#262b24", "#f4f3eb", "#b9b6a8", "#3d453a", "#8fae75"),
    "Earthy": ("#2a2420", "#342d28", "#f6f1e7", "#c4b8a6", "#4d4239", "#d98a5b"),
    "Sandstone": ("#2b2923", "#35332b", "#f7f4e9", "#c9c2ac", "#4a463a", "#d6b567"),
}
SYSTEM_THEME = "System"
THEME_CHOICES = [SYSTEM_THEME, *THEMES]


def blend_colours(top: QColor, bottom: QColor, weight: float) -> str:
    """Return a hex colour made of `weight` of `top` over the rest of `bottom`."""
    return QColor.fromRgbF(
        weight * top.redF() + (1 - weight) * bottom.redF(),
        weight * top.greenF() + (1 - weight) * bottom.greenF(),
        weight * top.blueF() + (1 - weight) * bottom.blueF(),
    ).name()


class AppCard(QFrame):
    details_requested = Signal(str)
    launch_requested = Signal(str)
    install_requested = Signal(str)
    shortcut_requested = Signal(str)
    rollback_requested = Signal(str)
    remove_requested = Signal(str)
    preferences_requested = Signal(str)
    build_requested = Signal(str)

    def __init__(self, app: App, icon_path: Path | None = None) -> None:
        super().__init__()
        self.app = app
        self.installed: Installed | None = None
        self.build_required = False
        self.setObjectName("appCard")
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self.icon = icon_label(app, 40, icon_path)
        top.addWidget(self.icon, alignment=Qt.AlignmentFlag.AlignTop)
        identity = QVBoxLayout()
        identity.setSpacing(3)
        name = QPushButton(app.name)
        name.setObjectName("appName")
        name.setAccessibleName(f"View {app.name} details")
        name.setToolTip(app.name)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setCursor(Qt.CursorShape.PointingHandCursor)
        name.clicked.connect(lambda: self.details_requested.emit(app.id))
        identity.addWidget(name)
        description = QLabel(app.description)
        description.setTextFormat(Qt.TextFormat.PlainText)
        description.setObjectName("cardDescription")
        description.setWordWrap(True)
        description.setMinimumWidth(0)
        identity.addWidget(description)
        top.addLayout(identity, stretch=1)
        self.update_button = QPushButton("Update")
        self.update_button.setObjectName("cardAction")
        self.update_button.setAccessibleName(f"Update {app.name}")
        self.update_button.clicked.connect(self.request_update)
        self.update_button.hide()
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
        self.preferences_action = menu.addAction("Application preferences…")
        self.preferences_action.triggered.connect(lambda: self.preferences_requested.emit(app.id))
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
        layout.addStretch()
        divider = QFrame()
        divider.setObjectName("cardDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        self.available_version = VersionLabel()
        self.available_row = QHBoxLayout()
        self.available_row.setSpacing(8)
        self.available_row.addWidget(self.available_version, stretch=1)
        self.available_row.addWidget(self.update_button)
        layout.addLayout(self.available_row)
        self.installed_version = VersionLabel()
        self.status = self.installed_version.text
        self.installed_row = QHBoxLayout()
        self.installed_row.setSpacing(8)
        self.installed_row.addWidget(self.installed_version, stretch=1)
        layout.addLayout(self.installed_row)
        self.action_button = QPushButton()
        self.action_button.setObjectName("cardAction")
        self.action_button.clicked.connect(self.activate)
        self.available_row.addWidget(self.action_button)
        self.checked = QLabel("Release information not checked")
        self.checked.setObjectName("cardChecked")
        self.checked.setWordWrap(True)
        layout.addWidget(self.checked)

    def activate(self) -> None:
        if self.installed is not None:
            self.launch_requested.emit(self.app.id)
        else:
            self.install_requested.emit(self.app.id)

    def request_update(self) -> None:
        if self.build_required:
            self.build_requested.emit(self.app.id)
        else:
            self.install_requested.emit(self.app.id)

    def set_state(
        self,
        installed: Installed | None,
        release: Release | None,
        busy: bool,
        error: str = "",
        info: ReleaseInfo | None = None,
        refreshing: bool = False,
        refresh_error: str = "",
    ) -> None:
        self.installed = installed
        action_row = self.installed_row if installed else self.available_row
        if action_row.indexOf(self.action_button) < 0:
            action_row.addWidget(self.action_button)
        newer = bool(installed and release and release.newer_than(installed.version))
        choice = info.choice(self.app.include_prereleases) if info else None
        self.build_required = bool(choice and choice.build_required)
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
        self.update_button.setText("Choose build" if self.build_required else "Update")
        self.update_button.setVisible(bool(installed and self.build_required) or newer)
        self.update_button.setEnabled(not busy)
        self.preferences_action.setEnabled(not busy)
        waiting = bool(
            installed
            and installed.prerelease
            and not self.app.include_prereleases
            and (info is not None or release is not None)
            and not newer
            and (release is None or release.version != installed.version)
        )
        self.available_version.setVisible(bool(newer or not installed or self.build_required))
        if not installed:
            self.available_version.set_version("Available")
        elif release:
            self.available_version.set_version(
                f"v{release.version}",
                release.prerelease,
                release.is_latest,
                caption="Update" if newer else "Available",
            )
        else:
            choice = info.choice(self.app.include_prereleases) if info else None
            self.available_version.set_version(
                f"v{choice.version} unavailable"
                if choice
                else "Only previews available"
                if info and info.stable is None
                else "Release unavailable"
                if error or refresh_error
                else "Checking releases…"
                if refreshing
                else "Release not checked"
            )
        self.available_version.setToolTip(error if installed else "Release checked when installing")
        self.installed_version.setVisible(installed is not None)
        if installed:
            latest = info.stable.release if info and info.stable else release
            self.installed_version.set_version(
                f"v{installed.version}" if installed.version else "Unknown",
                installed.prerelease and not newer,
                bool(
                    not newer
                    and latest
                    and latest.version == installed.version
                    and latest.is_latest
                ),
                caption="Installed",
            )
            self.status.setToolTip(f"Installed with AppGlider\n{installed.path}")
        stamp = datetime.fromtimestamp(info.checked_at).strftime("%d %b, %H:%M") if info else ""
        if refreshing:
            checked = "Refreshing releases…"
        elif refresh_error:
            checked = f"Refresh failed · checked {stamp}" if stamp else "Release check failed"
        elif self.build_required:
            checked = "Choose a build before updating"
        elif waiting:
            checked = "Waiting for a newer stable release"
        elif info:
            checked = f"Checked {stamp}" + (" · refresh needed" if not info.fresh() else "")
        else:
            checked = "Release information not checked"
        self.checked.setText(checked)
        self.checked.setVisible(installed is not None)
        self.checked.setToolTip(
            refresh_error or error or (f"Last checked {stamp}" if stamp else "")
        )
        self.action_button.setToolTip(
            f"Launch {installed.path}"
            if installed
            else "Check the eligible release, then download and verify its AppImage"
        )
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
        for row in range(self.layout_grid.rowCount()):
            self.layout_grid.setRowStretch(row, 0)
        self.columns = max(1, (width + 16) // (CARD_MIN_WIDTH + 16))
        for index, card in enumerate(self.cards):
            self.layout_grid.addWidget(card, index // self.columns, index % self.columns)
        for column in range(self.columns):
            self.layout_grid.setColumnStretch(column, 1)
        rows = (len(self.cards) + self.columns - 1) // self.columns
        self.layout_grid.setRowStretch(rows, 1)


class DetailsDialog(QDialog):
    check_requested = Signal()

    def __init__(
        self,
        app: App,
        parent: QWidget,
        installed: Installed | None = None,
        release: Release | None = None,
        error: str = "",
        icon_path: Path | None = None,
        release_info: ReleaseInfo | None = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.installed = installed
        self.setWindowTitle(f"About {app.name}")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(18)
        heading = QHBoxLayout()
        heading.addWidget(icon_label(app, 64, icon_path))
        title = QLabel(app.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setObjectName("pageTitle")
        heading.addWidget(title)
        heading.addStretch()
        layout.addLayout(heading)
        description = QLabel(app.description)
        description.setTextFormat(Qt.TextFormat.PlainText)
        description.setWordWrap(True)
        layout.addWidget(description)
        info = QFormLayout()
        repo = QLabel(app.repo)
        repo.setTextFormat(Qt.TextFormat.PlainText)
        repo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addRow("Repository", repo)
        info.addRow("Format", QLabel("Linux AppImage"))
        if app.build_variant:
            build = QLabel(variant_label(app.build_variant))
            build.setTextFormat(Qt.TextFormat.PlainText)
            build.setWordWrap(True)
            info.addRow("Selected build", build)
        self.installed_label = VersionLabel()
        info.addRow("Installed version", self.installed_label)
        self.available_label = VersionLabel()
        info.addRow("Available release", self.available_label)
        info.addRow(
            "Release preference",
            QLabel(
                "Stable and prerelease versions"
                if app.include_prereleases
                else "Stable releases only"
            ),
        )
        self.stable_label = VersionLabel()
        self.preview_label = VersionLabel()
        info.addRow("Stable release", self.stable_label)
        info.addRow("Preview release", self.preview_label)
        self.release_form = info
        if installed:
            location = QLabel(str(installed.path))
            location.setWordWrap(True)
            location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info.addRow("Location", location)
            filename = QLabel(installed.path.name)
            filename.setTextFormat(Qt.TextFormat.PlainText)
            filename.setWordWrap(True)
            info.addRow("Installed file", filename)
        layout.addLayout(info)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.check_button = buttons.addButton(
            "Check releases", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.check_button.clicked.connect(self.check_requested.emit)
        source = buttons.addButton("Open repository", QDialogButtonBox.ButtonRole.ActionRole)
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(app.source_url)))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.set_release_info(release, release_info, error)

    def set_release_info(
        self, release: Release | None, info: ReleaseInfo | None, error: str = ""
    ) -> None:
        installed = self.installed
        latest = info.stable.release if info and info.stable else release
        self.installed_label.set_version(
            installed.version or "Unknown" if installed else "Not installed",
            bool(installed and installed.prerelease),
            bool(installed and latest and installed.version == latest.version and latest.is_latest),
        )
        self.available_label.set_version(
            release.version if release else "Not checked" if info is None else "Unavailable",
            bool(release and release.prerelease),
            bool(release and release.is_latest),
        )
        self.available_label.setToolTip(release.name if release else "")
        for label, option in (
            (self.stable_label, info.stable if info else None),
            (self.preview_label, info.preview if info else None),
        ):
            self.release_form.setRowVisible(
                label, option is not None and (release is None or option.version != release.version)
            )
            if option:
                label.set_version(
                    option.version + (" · unavailable" if option.release is None else ""),
                    bool(option.release and option.release.prerelease),
                    bool(option.release and option.release.is_latest),
                )
                label.setToolTip(option.error)
        self.note.setText(
            error
            or "Check releases to inspect stable and preview versions without downloading an app."
        )

    def release_check_finished(self, result: TaskResult) -> None:
        info = result.release_infos.get(self.app.id)
        if info:
            choice = info.choice(self.app.include_prereleases)
            self.set_release_info(
                choice.release if choice else None, info, info.error(self.app.include_prereleases)
            )
        else:
            self.note.setText(result.message)
        self.check_button.setEnabled(True)
        self.check_button.setText("Check releases")

    def release_check_failed(self, error: str) -> None:
        self.note.setText(error)
        self.check_button.setEnabled(True)
        self.check_button.setText("Check releases")


class OrphansDialog(QDialog):
    """Offer to forget library entries for unknown apps; keeping them changes nothing."""

    def __init__(self, orphans: list[Orphan], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unrecognised library entries")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "These entries are in your AppGlider library but match no application in the "
            "current catalog, for example a repository that was removed. They are kept "
            "untouched unless you tick them. Forgetting an entry never deletes files."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.entries = QListWidget()
        self.entries.setAccessibleName("Unrecognised entries")
        for orphan in orphans:
            version = f" v{orphan.version}" if orphan.version else ""
            item = QListWidgetItem(f"{orphan.app_id}{version} · {orphan.path}")
            item.setData(Qt.ItemDataRole.UserRole, orphan.app_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.entries.addItem(item)
        layout.addWidget(self.entries)
        buttons = QDialogButtonBox()
        self.forget_button = buttons.addButton(
            "Forget ticked entries", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton("Keep all", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> set[str]:
        return {
            str(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self.entries.count())
            if (item := self.entries.item(row)).checkState() == Qt.CheckState.Checked
        }


class SettingsDialog(QDialog):
    repositories_requested = Signal()

    def __init__(self, settings: QSettings, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(700)
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        title = QLabel("Settings")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        content = QHBoxLayout()
        self.sections = QListWidget()
        self.sections.setObjectName("settingsSections")
        self.sections.setAccessibleName("Settings sections")
        self.sections.addItems(["General", "Applications", "Storage", "About"])
        self.sections.setFixedWidth(150)
        self.pages = QStackedWidget()
        self.sections.currentRowChanged.connect(self.pages.setCurrentIndex)
        content.addWidget(self.sections)
        content.addWidget(self.pages, stretch=1)
        layout.addLayout(content, stretch=1)
        page_layouts = []
        for _ in range(4):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 0, 0, 0)
            page_layout.setSpacing(16)
            self.pages.addWidget(page)
            page_layouts.append(page_layout)
        general, applications_page, storage, about = page_layouts
        appearance = QGroupBox("Appearance")
        appearance_form = QFormLayout(appearance)
        self.theme = QComboBox()
        self.theme.setItemDelegate(QStyledItemDelegate(self.theme))
        self.theme.setAccessibleName("Appearance")
        self.theme.addItems(THEME_CHOICES)
        current_theme = str(settings.value("appearance/theme", SYSTEM_THEME))
        self.theme.setCurrentText(current_theme if current_theme in THEME_CHOICES else SYSTEM_THEME)
        appearance_form.addRow("Appearance", self.theme)
        general.addWidget(appearance)
        self.close_to_tray = QCheckBox("Keep AppGlider in the tray when closing its window")
        self.close_to_tray.setChecked(
            bool(settings.value("general/close-to-tray", True, type=bool))
        )
        self.close_to_tray.setToolTip("Applies when a system tray is available. Use Quit to exit.")
        general.addWidget(self.close_to_tray)
        downloads = QGroupBox("Downloads")
        download_layout = QVBoxLayout(downloads)
        download_layout.addWidget(QLabel("Install new applications in"))
        default_folder = str(
            Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
            )
            / "appglider"
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
        storage.addWidget(downloads)
        applications = QGroupBox("Applications")
        application_layout = QHBoxLayout(applications)
        description = QLabel(
            "Choose release preferences, manage repositories and change application icons."
        )
        description.setObjectName("muted")
        description.setWordWrap(True)
        application_layout.addWidget(description, stretch=1)
        self.repositories_button = QPushButton("Repositories…")
        self.repositories_button.clicked.connect(self.repositories_requested.emit)
        application_layout.addWidget(self.repositories_button)
        applications_page.addWidget(applications)
        github = QGroupBox("GitHub requests")
        github_layout = QVBoxLayout(github)
        self.github_details = QLabel()
        self.github_details.setTextFormat(Qt.TextFormat.PlainText)
        self.github_details.setWordWrap(True)
        self.github_details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.github_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.github_details.setAccessibleName("GitHub request status")
        github_layout.addWidget(self.github_details)
        github_note = QLabel(
            "Status updates from existing requests; opening Settings makes no requests. "
            "Other applications on your network may share the request allowance."
        )
        github_note.setObjectName("muted")
        github_note.setWordWrap(True)
        github_layout.addWidget(github_note)
        applications_page.addWidget(github)
        self.github_timer = QTimer(self)
        self.github_timer.timeout.connect(self.update_github_status)
        self.finished.connect(self.github_timer.stop)
        self.github_timer.start(1000)
        self.update_github_status()
        app_title = QLabel(f"{APP_NAME} {APP_VERSION}")
        app_title.setObjectName("sectionTitle")
        about.addWidget(app_title)
        about_note = QLabel("A home for your Linux AppImages.\nWritten in Python with Qt.")
        about_note.setWordWrap(True)
        about.addWidget(about_note)
        for caption, url in (
            ("View AppGlider on GitHub", "https://github.com/Vyrnexis/AppGlider"),
            ("Report an issue", "https://github.com/Vyrnexis/AppGlider/issues"),
        ):
            button = QPushButton(caption)
            button.clicked.connect(
                lambda checked=False, url=url: QDesktopServices.openUrl(QUrl(url))
            )
            about.addWidget(button)
        for page_layout in page_layouts:
            page_layout.addStretch()
        self.sections.setCurrentRow(0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        layout.addWidget(buttons)

    def update_github_status(self) -> None:
        self.github_details.setText(github_request_status()[1])

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
            / "appglider"
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
        self.release_infos: dict[str, ReleaseInfo] = {}
        self.cache_stamps: dict[str, object] = {}
        self.tray_key: list[tuple[str, str, Path, tuple[int, int, int] | None]] | None = None
        self.release_errors: dict[str, str] = {}
        self.checks = ReleaseChecks(
            self.checkable_app,
            self.release_operation,
            lambda: 3 if github_status.quota_plentiful() else 1,
        )
        self.checks.started.connect(self.release_check_started)
        self.checks.completed.connect(self.release_check_done)
        self.checks.drained.connect(self.release_checks_drained)
        self.background_enabled = False
        self.close_after_release = False
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
        logo.setPixmap(self.windowIcon().pixmap(96, 96))
        logo.setFixedSize(96, 96)
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
        self.version_button = QPushButton(f"v{APP_VERSION}")
        self.version_button.setObjectName("versionButton")
        self.version_button.setAccessibleName(f"About AppGlider {APP_VERSION}")
        self.version_button.setToolTip("About AppGlider")
        self.version_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_button.clicked.connect(lambda: self.open_settings(3))
        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(lambda: self.open_settings())
        header.addWidget(settings_button)
        layout.addLayout(header)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setAccessibleName("Search apps")
        self.search.setClearButtonEnabled(True)
        search_row.addWidget(self.search, stretch=1)
        self.add_app_button = QPushButton("+ Add app")
        self.add_app_button.setObjectName("primary")
        self.add_app_button.clicked.connect(lambda: self.open_repositories(adding=True))
        search_row.addWidget(self.add_app_button)
        layout.addLayout(search_row)
        navigation = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setAccessibleName("Library filters")
        self.tabs.setDrawBase(False)
        for name in ("All apps", "Installed", "Updates"):
            self.tabs.addTab(name)
        navigation.addWidget(self.tabs)
        navigation.addStretch()
        self.update_all_button = QPushButton("Update all")
        self.update_all_button.clicked.connect(self.update_all)
        navigation.addWidget(self.update_all_button)
        self.check_button = QPushButton("Refresh releases")
        self.check_button.setToolTip(
            "Check installed applications for eligible stable and preview updates"
        )
        self.check_button.clicked.connect(lambda: self.refresh_releases(force=True))
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
        self.footer.setTextFormat(Qt.TextFormat.PlainText)
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
        self.orphans: list[Orphan] = []
        self.orphans_button = QPushButton()
        self.orphans_button.clicked.connect(self.review_orphans)
        self.orphans_button.hide()
        progress_row.addWidget(self.orphans_button)
        self.github_button = QPushButton()
        self.github_button.setObjectName("githubStatus")
        self.github_button.setAccessibleName("GitHub request status")
        self.github_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.github_button.clicked.connect(lambda: self.open_settings(1))
        progress_row.addWidget(self.github_button)
        progress_row.addWidget(self.version_button)
        layout.addLayout(progress_row)
        self.cards = {app.id: self.create_card(app) for app in self.apps}
        self.search.textChanged.connect(self.refresh)
        self.tabs.currentChanged.connect(self.refresh)
        QApplication.styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.scroll_area.viewport().installEventFilter(self)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction("Open AppGlider").triggered.connect(self.show_launcher)
        self.tray_check_action = self.tray_menu.addAction("Refresh releases")
        self.tray_check_action.triggered.connect(lambda: self.refresh_releases(force=True))
        self.tray_apps_menu = self.tray_menu.addMenu("Installed apps")
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Quit AppGlider").triggered.connect(self.quit_launcher)
        self.tray_menu.aboutToShow.connect(self.scan_installed)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.apply_theme()
        restore_rate_limits(self.library.data_dir / "release-cache")
        self.github_timer = QTimer(self)
        self.github_timer.timeout.connect(self.update_github_status)
        self.github_timer.start(1000)
        self.update_github_status()
        self.scan_installed()

    def update_github_status(self) -> None:
        label, details, limited = github_request_status()
        self.github_button.setText(label)
        self.github_button.setToolTip(details + "\nClick for details in Settings → Applications.")
        if self.github_button.property("limited") != limited:
            self.github_button.setProperty("limited", limited)
            self.github_button.style().unpolish(self.github_button)
            self.github_button.style().polish(self.github_button)

    def create_card(self, app: App) -> AppCard:
        card = AppCard(app, self.library.icon_path(app))
        card.setParent(self.grid)
        card.details_requested.connect(self.open_details)
        card.launch_requested.connect(self.launch_app)
        card.install_requested.connect(self.install_app)
        card.shortcut_requested.connect(self.create_shortcut)
        card.rollback_requested.connect(self.rollback_app)
        card.remove_requested.connect(self.remove_app)
        card.preferences_requested.connect(lambda app_id: self.open_repositories(app_id=app_id))
        card.build_requested.connect(self.choose_build)
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
        if not self.close() and not self.close_after_task and not self.close_after_release:
            self.quitting = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.scroll_area.viewport() and isinstance(event, QResizeEvent):
            width = event.size().width()
            if max(1, (width + 16) // (CARD_MIN_WIDTH + 16)) != self.grid.columns:
                self.grid.reflow(width)
        return super().eventFilter(watched, event)

    def refresh(self) -> None:
        update_count = sum(self.has_update(app.id) for app in self.apps)
        self.tabs.setTabText(1, f"Installed ({len(self.installed)})")
        self.tabs.setTabText(2, f"Updates ({update_count})")
        self.tray.setToolTip(f"AppGlider · Updates: {update_count}")
        self.tray_check_action.setEnabled(not self.checks.busy and bool(self.installed))
        tray_apps = [
            (app, self.library.icon_path(app)) for app in self.apps if app.id in self.installed
        ]
        tray_key = [(app.id, app.name, path, file_stamp(path)) for app, path in tray_apps]
        if tray_key != self.tray_key:
            self.tray_key = tray_key
            self.tray_apps_menu.clear()
            self.tray_apps_menu.setEnabled(bool(tray_apps))
            for app, path in tray_apps:
                action = self.tray_apps_menu.addAction(QIcon(icon_pixmap(path, 64)), app.name)
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
            card.icon.setPixmap(icon_pixmap(self.library.icon_path(card.app), 40))
            card.set_state(
                self.installed.get(app_id),
                self.releases.get(app_id),
                self.task is not None,
                self.check_errors.get(app_id, ""),
                self.release_infos.get(app_id),
                self.checks.is_checking(app_id),
                self.release_errors.get(app_id, ""),
            )
            card.setVisible(app_id in ids)
        self.add_app_button.setEnabled(self.task is None)
        self.check_button.setText("Refreshing…" if self.checks.busy else "Refresh releases")
        self.check_button.setEnabled(not self.checks.busy and bool(self.installed))
        self.update_all_button.setVisible(
            any(
                item.managed and app_id in self.releases and self.has_update(app_id)
                for app_id, item in self.installed.items()
            )
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
            self.empty.setText(
                "No known updates.\nUse Refresh releases to look for new releases."
                if self.installed
                else "No installed apps to check.\nInstall an app from All apps to get started."
            )
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
            release_info=self.release_infos.get(app_id),
        )
        dialog.check_button.setEnabled(self.task is None)

        def check_releases() -> None:
            if self.task is not None:
                return

            def operation(cancel: Event, progress: Progress) -> TaskResult:
                info = discover_releases(
                    app, cancel, self.library.data_dir / "release-cache", force=True
                )
                return TaskResult("Release checked.", release_infos={app.id: info})

            dialog.check_button.setEnabled(False)
            dialog.check_button.setText("Checking…")
            self.start_task(f"Checking releases for {app.name}…", operation, details=dialog)

        dialog.check_requested.connect(check_releases)
        dialog.exec()
        dialog.deleteLater()

    def open_settings(self, section: int = 0) -> None:
        dialog = SettingsDialog(self.settings, self)
        dialog.sections.setCurrentRow(section)
        dialog.repositories_button.setEnabled(self.task is None)
        dialog.repositories_requested.connect(lambda: self.open_repositories())
        dialog.theme.currentTextChanged.connect(self.apply_theme)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if not accepted:
            self.apply_theme()
        if accepted:
            self.settings.setValue("general/close-to-tray", dialog.close_to_tray.isChecked())
            self.settings.setValue("appearance/theme", dialog.theme.currentText())
            self.settings.setValue("installation/folder", dialog.folder.text())
            self.library.apps_dir = Path(dialog.folder.text())
            self.apply_theme()
            self.scan_installed()

    def choose_build(self, app_id: str) -> None:
        self.open_repositories(app_id=app_id, choose_build=True)

    def review_orphans(self) -> None:
        if self.task is not None:
            return
        dialog = OrphansDialog(self.orphans, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected():
            try:
                self.library.discard_orphans(dialog.selected())
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Cannot update the library", str(error))
            self.scan_installed()
        dialog.deleteLater()

    def open_repositories(
        self, adding: bool = False, app_id: str | None = None, *, choose_build: bool = False
    ) -> None:
        if self.task is not None:
            return
        try:
            dialog = RepositoriesDialog(self.library, self)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot open repositories", str(error))
            return
        dialog.catalog_changed.connect(self.catalog_changed)
        if adding:
            dialog.start_add_repository()
        elif app_id:
            for row in range(dialog.repo_list.count()):
                if dialog.repo_list.item(row).data(Qt.ItemDataRole.UserRole) == app_id:
                    dialog.repo_list.setCurrentRow(row)
                    break
            if choose_build:
                dialog.choose_build()
        dialog.exec()

    def has_update(self, app_id: str) -> bool:
        installed, release = self.installed.get(app_id), self.releases.get(app_id)
        if installed and release:
            return release.newer_than(installed.version)
        info = self.release_infos.get(app_id)
        app = next((app for app in self.apps if app.id == app_id), None)
        choice = info.choice(app.include_prereleases) if info and app else None
        return bool(
            installed
            and choice
            and choice.build_required
            and version_key(choice.version) > version_key(installed.version)
        )

    def scan_installed(self) -> None:
        try:
            apps = self.library.apps
            installed = self.library.scan()
            orphans = self.library.orphans()
            self.library.reap_processes()
        except (OSError, ValueError) as error:
            self.footer.setText(str(error))
        else:
            self.orphans = orphans
            self.orphans_button.setVisible(bool(orphans))
            self.orphans_button.setText(
                f"Review {len(orphans)} unrecognised {'entry' if len(orphans) == 1 else 'entries'}…"
            )
            if apps != self.apps:
                for app_id in set(self.cards) - {app.id for app in apps}:
                    self.cards.pop(app_id).deleteLater()
                    self.releases.pop(app_id, None)
                    self.check_errors.pop(app_id, None)
                    self.release_infos.pop(app_id, None)
                    self.release_errors.pop(app_id, None)
                for app in apps:
                    if app.id not in self.cards or self.cards[app.id].app != app:
                        old = self.cards.pop(app.id, None)
                        if old is not None:
                            if old.app.build_variant != app.build_variant:
                                self.release_infos.pop(app.id, None)
                                self.release_errors.pop(app.id, None)
                                self.checks.requeue_active(app.id)
                            old.deleteLater()
                        self.releases.pop(app.id, None)
                        self.check_errors.pop(app.id, None)
                        self.cards[app.id] = self.create_card(app)
                self.apps = apps
            cache_dir = self.library.data_dir / "release-cache"
            for app in apps:
                paths = (info_path(app, cache_dir), release_cache_path(app, cache_dir))
                stamps = tuple(file_stamp(path) for path in paths)
                fresh = self.cache_stamps.get(app.id) != (paths, stamps)
                self.cache_stamps[app.id] = (paths, stamps)
                info = cached_info(app, cache_dir) if fresh else None
                if info is not None and (
                    app.id not in self.release_infos
                    or info.checked_at > self.release_infos[app.id].checked_at
                ):
                    self.release_infos[app.id] = info
                    github_status.release_result("Cached information", app.repo, info.checked_at)
                if app.id in self.release_infos:
                    self.apply_release_info(app)
                elif fresh:
                    release = cached_release(app, cache_dir)
                    if release is not None:
                        self.releases[app.id] = release
            self.installed = installed
            if self.task is None:
                self.footer.setText(f"{len(self.installed)} apps installed with AppGlider.")
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

        QTimer.singleShot(1800, self, check_startup)

    def start_task(
        self,
        message: str,
        operation: Callable[[Event, Progress], TaskResult],
        *,
        details: DetailsDialog | None = None,
    ) -> None:
        if self.task is not None:
            return
        self.task = Task(operation)
        if details is not None:
            self.task.signals.finished.connect(details.release_check_finished)
            self.task.signals.failed.connect(details.release_check_failed)
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
            self.footer.setText(f"Created menu shortcut: {app.name} (AppGlider).")

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
        for app in self.apps:
            checked_info = result.release_infos.get(app.id)
            if checked_info is not None and checked_info.build_variant == app.build_variant:
                self.release_infos[app.id] = checked_info
                self.release_errors.pop(app.id, None)
                self.apply_release_info(app)
            release = result.releases.get(app.id)
            if release is not None:
                info = remember_release(app, release, self.library.data_dir / "release-cache")
                if info is not None:
                    self.release_infos[app.id] = info
        self.check_errors.update(result.errors)
        for app_id in result.releases:
            self.check_errors.pop(app_id, None)
        self.reset_task()
        self.footer.setText(result.message)
        if result.build_choices and not self.close_after_task:
            self.choose_build(next(iter(result.build_choices)))
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

    def apply_release_info(self, app: App) -> None:
        info = self.release_infos[app.id]
        choice = info.choice(app.include_prereleases)
        if choice and choice.release:
            self.releases[app.id] = choice.release
        else:
            self.releases.pop(app.id, None)
        error = info.error(app.include_prereleases)
        if error:
            self.check_errors[app.id] = error
        else:
            self.check_errors.pop(app.id, None)

    def catalog_changed(self) -> None:
        self.scan_installed()
        if self.background_enabled:
            self.checks.add(
                [
                    app.id
                    for app in self.apps
                    if app.id in self.installed and app.id not in self.release_infos
                ]
            )

    def refresh_releases(self, *, force: bool = False) -> None:
        if self.checks.busy or self.close_after_release:
            return
        self.background_enabled = True
        self.scan_installed()
        if not self.installed:
            self.footer.setText("Install an application to enable release checks.")
            return
        self.checks.force = force
        self.checks.add([app.id for app in self.apps if app.id in self.installed])

    def release_operation(self, app: App, force: bool) -> Callable[[Event, Progress], TaskResult]:
        def operation(cancel: Event, progress: Progress) -> TaskResult:
            info = discover_releases(
                app, cancel, self.library.data_dir / "release-cache", force=force
            )
            return TaskResult("Release checked.", release_infos={app.id: info})

        return operation

    def checkable_app(self, app_id: str) -> App | None:
        if app_id not in self.installed:
            return None
        return next((app for app in self.apps if app.id == app_id), None)

    def release_check_started(self, app_id: str) -> None:
        if self.task is None:
            app = next(app for app in self.apps if app.id == app_id)
            self.footer.setText(f"Checking releases for {app.name}…")
        self.refresh()

    def release_check_done(
        self, app_id: str, variant: str, result: TaskResult | None, error: str
    ) -> None:
        if result is None:
            app = next((app for app in self.apps if app.id == app_id), None)
            if app and app.build_variant == variant:
                self.release_errors[app.id] = error
            return
        for app in self.apps:
            info = result.release_infos.get(app.id)
            if info is not None:
                if info.build_variant != app.build_variant:
                    self.checks.add([app.id])
                    continue
                self.release_infos[app.id] = info
                self.release_errors.pop(app.id, None)
                self.apply_release_info(app)

    def release_checks_drained(self) -> None:
        if self.close_after_release:
            self.close()
            return
        self.refresh()
        if self.task is None:
            self.footer.setText(
                "Release refresh finished."
                + (
                    " Some checks failed; cards retain previously known information."
                    if any(app_id in self.installed for app_id in self.release_errors)
                    else ""
                )
            )

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
            app
            for app in self.apps
            if self.has_update(app.id)
            and app.id in self.releases
            and self.installed[app.id].managed
        ]

        versions = {app.id: self.installed[app.id].version for app in apps}

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            result = TaskResult("Updates finished.")
            updated: list[str] = []
            for app in apps:
                check_cancel(cancel)
                progress(f"Checking {app.name}…", 0, 0)
                try:
                    release = latest_release(app, cancel, self.library.data_dir / "release-cache")
                    if not release.newer_than(versions[app.id]):
                        result.releases[app.id] = release
                        continue
                    self.library.install(
                        app,
                        release,
                        cancel,
                        lambda message, done, total: progress(
                            f"{app.name} · {message}", done, total
                        ),
                    )
                    result.releases[app.id] = release
                    updated.append(app.id)
                except BuildChoiceRequired as error:
                    result.build_choices[app.id] = error
                except (OSError, ValueError) as error:
                    result.errors[app.id] = str(error)
            result.message = f"Updated {len(updated)} apps."
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
            "Remove this app and its previous version from AppGlider?\n"
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
        if (
            not self.quitting
            and self.settings.value("general/close-to-tray", True, type=bool)
            and self.tray.isVisible()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
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
        if self.checks.busy:
            self.quitting = True
            self.close_after_release = True
            self.checks.stop()
            self.tray.hide()
            self.hide()
            event.ignore()
            return
        self.tray.hide()
        super().closeEvent(event)

    def apply_theme(self, preview: str | None = None) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        theme = preview or str(self.settings.value("appearance/theme", SYSTEM_THEME))
        if theme in THEMES:
            background, surface, foreground, muted, border, accent = THEMES[theme]
            on_accent = background
        else:
            app.setPalette(QPalette())
            native = app.palette()
            window = native.color(QPalette.ColorRole.Window)
            text = native.color(QPalette.ColorRole.WindowText)
            background, foreground = window.name(), text.name()
            surface = native.color(QPalette.ColorRole.Base).name()
            muted = blend_colours(text, window, 0.35)
            border = blend_colours(text, window, 0.2)
            accent = native.color(QPalette.ColorRole.Highlight).name()
            on_accent = native.color(QPalette.ColorRole.HighlightedText).name()
        dark = QColor(background).lightness() < 128
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
            (QPalette.ColorRole.HighlightedText, on_accent),
            (QPalette.ColorRole.PlaceholderText, muted),
        ):
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(muted))
        if theme in THEMES:
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
            QFrame#cardDivider {{ border: none; background: {border}; }}
            QLabel#cardDescription, QLabel#versionCaption {{ color: {muted}; font-size: 12px; }}
            QLabel#cardChecked {{ color: {muted}; font-size: 11px; }}
            QPushButton#cardAction {{ padding: 7px 12px; font-size: 13px; }}
            QPushButton, QToolButton {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 9px 14px; }}
            QPushButton:hover, QToolButton:hover {{ border-color: {accent}; color: {accent}; }}
            QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus {{
                border: 2px solid {accent}; }}
            QPushButton:disabled {{ color: {muted}; background: {background}; }}
            QPushButton#primary {{ background: {accent}; color: {on_accent};
                border-color: {accent}; font-weight: 600; }}
            QPushButton#primary:hover {{ border-color: {foreground}; }}
            QPushButton#primary:disabled {{ background: {surface}; color: {muted};
                border-color: {border}; }}
            QPushButton#danger {{ color: {"#ff9d9d" if dark else "#a52626"}; }}
            QPushButton#appName {{ border: none; padding: 0; text-align: left;
                font-size: 17px; font-weight: 650; background: transparent; }}
            QPushButton#appName:focus {{ outline: 2px solid {accent}; }}
            QToolButton#cardMenu {{ border: none; background: transparent;
                font-size: 24px; padding: 0px 6px; }}
            QToolButton::menu-indicator {{ image: none; }}
            QLabel#status {{ color: {muted}; font-size: 12px; }}
            QLabel#releaseBadge {{ border: 1px solid; border-radius: 9px;
                padding: 2px 7px; font-size: 11px; font-weight: 600; }}
            QLabel#releaseBadge[release="latest"] {{ color: {"#7ee2a8" if dark else "#18783c"};
                border-color: {"#3c8159" if dark else "#89bd9b"}; }}
            QLabel#releaseBadge[release="prerelease"] {{ color: {"#f0c46e" if dark else "#895b00"};
                border-color: {"#96753b" if dark else "#c7a660"}; }}
            QPushButton#versionButton, QPushButton#githubStatus {{
                background: transparent; border: 1px solid transparent;
                color: {muted}; font-size: 12px; padding: 5px 10px; }}
            QPushButton#versionButton:hover, QPushButton#versionButton:focus,
            QPushButton#githubStatus:hover, QPushButton#githubStatus:focus {{
                background: {surface}; border-color: {accent}; color: {accent}; }}
            QPushButton#githubStatus[limited="true"] {{
                color: {"#ffd079" if dark else "#845300"}; }}
            QListWidget#settingsSections {{ border: none; background: transparent; outline: 0; }}
            QListWidget#settingsSections::item {{ padding: 12px; border-radius: 7px; }}
            QListWidget#settingsSections::item:selected {{
                background: {surface}; color: {accent}; }}
            QLineEdit, QComboBox {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; border-radius: 7px; padding: 9px; }}
            QComboBox QAbstractItemView {{ background: {surface}; color: {foreground};
                border: 1px solid {border}; padding: 4px; outline: 0;
                selection-background-color: {accent}; selection-color: {on_accent}; }}
            QComboBox QAbstractItemView::item {{ min-height: 32px; padding: 4px 10px; }}
            QComboBox QAbstractItemView::item:selected {{ background: {accent};
                color: {on_accent}; }}
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
