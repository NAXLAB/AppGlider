"""Inspect and save custom GitHub repositories without blocking the desktop UI."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QCloseEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from appharbour import APP_ICON
from appharbour.catalog import APPS, App, custom_app
from appharbour.icons import (
    MAX_ICON_BYTES,
    IconCandidate,
    discover_icons,
    icon_pixmap,
    normalise_icon,
)
from appharbour.jobs import Task, TaskResult
from appharbour.library import Library
from appharbour.releases import Progress, Release, architecture, latest_release


class RepositoriesDialog(QDialog):
    catalog_changed = Signal()

    def __init__(self, library: Library, parent: QWidget) -> None:
        super().__init__(parent)
        self.library = library
        self.task: Task | None = None
        self.candidate: App | None = None
        self.release: Release | None = None
        self.icon_target: App | None = None
        self.icon_options: tuple[IconCandidate, ...] = ()
        self.selected_icon: bytes | None = None
        self.closing = False
        self.adding = False
        self.setWindowTitle("Repositories")
        self.setObjectName("repositoriesDialog")
        self.setMinimumWidth(800)
        self.resize(960, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Your applications")
        title.setObjectName("dialogTitle")
        heading.addWidget(title)
        note = QLabel("Manage repositories and choose application icons.")
        note.setObjectName("muted")
        heading.addWidget(note)
        header.addLayout(heading, stretch=1)
        self.new_repository_button = QPushButton("Add repository")
        self.new_repository_button.setObjectName("primary")
        self.new_repository_button.clicked.connect(self.start_add_repository)
        header.addWidget(self.new_repository_button)
        layout.addLayout(header)
        content = QHBoxLayout()
        content.setSpacing(24)
        self.repo_list = QListWidget()
        self.repo_list.setObjectName("repositories")
        self.repo_list.setAccessibleName("Repositories")
        self.repo_list.setIconSize(QSize(32, 32))
        self.repo_list.setMinimumWidth(230)
        self.repo_list.currentRowChanged.connect(self.select_repository)
        content.addWidget(self.repo_list, stretch=2)
        editor = QVBoxLayout()
        editor.setSpacing(12)
        self.details_page = QWidget()
        details = QVBoxLayout(self.details_page)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(12)
        selected = QHBoxLayout()
        self.detail_icon = QLabel()
        self.detail_icon.setFixedSize(64, 64)
        selected.addWidget(self.detail_icon)
        identity = QVBoxLayout()
        self.detail_name = QLabel()
        self.detail_name.setObjectName("sectionTitle")
        self.detail_name.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_name.setWordWrap(True)
        self.detail_source = QLabel()
        self.detail_source.setObjectName("muted")
        self.detail_source.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_source.setWordWrap(True)
        identity.addWidget(self.detail_name)
        identity.addWidget(self.detail_source)
        selected.addLayout(identity, stretch=1)
        details.addLayout(selected)
        self.detail_note = QLabel()
        self.detail_note.setWordWrap(True)
        self.detail_note.setObjectName("muted")
        details.addWidget(self.detail_note)
        editor.addWidget(self.details_page)
        self.add_page = QWidget()
        add_layout = QVBoxLayout(self.add_page)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(8)
        add_title = QLabel("Add an application")
        add_title.setObjectName("sectionTitle")
        add_layout.addWidget(add_title)
        add_note = QLabel("Paste a public GitHub repository to find its latest AppImage.")
        add_note.setObjectName("muted")
        add_note.setWordWrap(True)
        add_layout.addWidget(add_note)
        form = QFormLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://github.com/owner/application")
        self.url.setAccessibleName("GitHub repository")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Optional display name")
        self.name.setAccessibleName("Application name")
        self.url.textChanged.connect(self.invalidate_preview)
        self.name.textChanged.connect(self.invalidate_preview)
        form.addRow("Repository", self.url)
        form.addRow("Name", self.name)
        add_layout.addLayout(form)
        self.inspect_button = QPushButton("Inspect repository")
        self.inspect_button.clicked.connect(self.inspect_repository)
        self.inspect_button.setAutoDefault(False)
        add_layout.addWidget(self.inspect_button)
        editor.addWidget(self.add_page)
        self.add_page.hide()
        self.release_preview = QGroupBox("Release found")
        preview = QFormLayout(self.release_preview)
        self.version = QLabel("Not inspected")
        self.filename = QLabel("—")
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.setWordWrap(True)
        self.filename.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.checksum = QLabel("Not checked")
        self.checksum.setWordWrap(True)
        self.checksum.setObjectName("muted")
        preview.addRow("Version", self.version)
        preview.addRow("Download", self.filename)
        preview.addRow("Verification", self.checksum)
        editor.addWidget(self.release_preview)
        self.icon_group = QGroupBox("Application icon")
        icon_row = QHBoxLayout(self.icon_group)
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(64, 64)
        self.icon_preview.setAccessibleName("Application icon preview")
        icon_row.addWidget(self.icon_preview)
        icon_controls = QVBoxLayout()
        self.icon_choices = QComboBox()
        self.icon_choices.setAccessibleName("Application icon")
        self.icon_choices.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.icon_choices.setMinimumContentsLength(12)
        self.icon_choices.currentIndexChanged.connect(self.select_icon)
        icon_controls.addWidget(self.icon_choices)
        icon_actions = QHBoxLayout()
        self.find_icons_button = QPushButton("Find icons")
        self.find_icons_button.clicked.connect(self.find_existing_icons)
        icon_actions.addWidget(self.find_icons_button)
        self.choose_icon_button = QPushButton("Choose icon…")
        self.choose_icon_button.clicked.connect(self.choose_icon)
        icon_actions.addWidget(self.choose_icon_button)
        icon_controls.addLayout(icon_actions)
        icon_row.addLayout(icon_controls, stretch=1)
        editor.addWidget(self.icon_group)
        editor.addStretch()
        actions = QHBoxLayout()
        self.remove_button = QPushButton("Remove repository")
        self.remove_button.setObjectName("danger")
        self.remove_button.clicked.connect(self.remove_repository)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        self.add_button = QPushButton("Add to library")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self.add_repository)
        actions.addWidget(self.add_button)
        editor.addLayout(actions)
        content.addLayout(editor, stretch=4)
        layout.addLayout(content, stretch=1)
        footer = QHBoxLayout()
        self.status = QLabel("Repository and icon changes save immediately.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        footer.addWidget(self.status, stretch=1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        self.refresh_list()
        self.repo_list.setCurrentRow(0)
        self.set_busy(False)

    def refresh_list(self) -> None:
        apps = self.library.apps
        self.repo_list.blockSignals(True)
        self.repo_list.clear()
        builtin_ids = {app.id for app in APPS}
        for app in sorted(apps, key=lambda app: (app.id in builtin_ids, app.name.casefold())):
            item = QListWidgetItem(f"{app.name}\n{app.repo}")
            item.setToolTip("Built-in application" if app.id in builtin_ids else app.source_url)
            item.setIcon(QIcon(icon_pixmap(self.library.icon_path(app), 32)))
            item.setData(Qt.ItemDataRole.UserRole, app.id)
            self.repo_list.addItem(item)
        self.repo_list.blockSignals(False)
        self.update_remove_button()

    def update_remove_button(self) -> None:
        item = self.repo_list.currentItem()
        app_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        custom = app_id is not None and app_id not in {app.id for app in APPS}
        enabled = self.task is None and custom
        self.remove_button.setVisible(not self.adding and custom)
        self.remove_button.setEnabled(enabled)
        self.find_icons_button.setVisible(not self.adding)
        self.find_icons_button.setEnabled(
            enabled and self.icon_target is not None and self.candidate is None
        )

    def select_repository(self) -> None:
        if self.task is not None or not hasattr(self, "icon_choices"):
            return
        self.adding = False
        self.details_page.show()
        self.add_page.hide()
        self.invalidate_preview()
        item = self.repo_list.currentItem()
        selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        app = next((app for app in self.library.apps if app.id == selected), None)
        self.icon_target = app if app and app.asset_stem else None
        self.detail_name.setText(app.name if app else "Select an application")
        self.detail_source.setText(app.repo if app else "")
        self.detail_note.setText(
            "Choose an icon for the library, tray and application menu."
            if self.icon_target
            else "Included with AppHarbour. This repository is managed by the built-in catalog."
        )
        self.set_icon_options(())
        if self.icon_target:
            path = self.library.icon_path(self.icon_target)
            if path != APP_ICON:
                try:
                    self.set_icon_options((IconCandidate("Saved icon", path.read_bytes()),))
                except OSError:
                    self.show_icon(APP_ICON)
            else:
                self.show_icon(path)
            self.status.setText("Repository and icon changes save immediately.")
        elif app:
            self.show_icon(app.icon)
            self.icon_choices.setItemText(0, "Built-in icon")
            self.status.setText("Built-in application icons are included with AppHarbour.")
        self.set_busy(False)

    def start_add_repository(self) -> None:
        if self.task is not None:
            return
        self.adding = True
        self.repo_list.blockSignals(True)
        self.repo_list.setCurrentRow(-1)
        self.repo_list.clearSelection()
        self.repo_list.blockSignals(False)
        self.details_page.hide()
        self.add_page.show()
        self.url.clear()
        self.name.clear()
        self.invalidate_preview()
        self.url.setFocus()
        self.set_busy(False)

    def invalidate_preview(self) -> None:
        self.candidate = None
        self.release = None
        self.icon_target = None
        self.set_icon_options(())
        self.choose_icon_button.setEnabled(False)
        self.icon_choices.setEnabled(False)
        self.add_button.setEnabled(False)
        self.version.setText("Not inspected")
        self.filename.setText("—")
        self.checksum.setText("Not checked")
        self.status.setText("Inspect this repository before adding it.")
        self.release_preview.hide()
        self.icon_group.hide()
        self.update_remove_button()

    def inspect_repository(self) -> None:
        if self.task is not None:
            return
        self.invalidate_preview()
        try:
            app = custom_app(self.url.text(), self.name.text() or None)
            if any(known.repo.casefold() == app.repo.casefold() for known in self.library.apps):
                raise ValueError("This repository is already in the catalog.")
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.name.setText(app.name)
        self.candidate = app
        self.icon_target = app

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            release = latest_release(app, cancel, self.library.data_dir / "release-cache")
            result = TaskResult("Release inspected.", {app.id: release})
            try:
                result.icons = discover_icons(app, cancel)
            except (OSError, ValueError):
                result.message = "Release inspected. Icon lookup unavailable."
            return result

        self.task = Task(operation)
        self.task.signals.finished.connect(self.inspection_finished)
        self.task.signals.failed.connect(self.inspection_failed)
        self.set_busy(True)
        self.status.setText("Checking the latest stable release and published checksum…")
        QThreadPool.globalInstance().start(self.task)

    def set_busy(self, busy: bool) -> None:
        self.repo_list.setEnabled(not busy)
        self.url.setEnabled(not busy)
        self.name.setEnabled(not busy)
        self.inspect_button.setEnabled(not busy)
        self.inspect_button.setText("Inspecting…" if busy else "Inspect repository")
        self.new_repository_button.setEnabled(not busy and not self.adding)
        self.add_button.setVisible(self.adding)
        self.add_button.setEnabled(not busy and self.release is not None)
        self.release_preview.setVisible(self.adding and self.release is not None)
        self.icon_group.setVisible(
            self.icon_target is not None and (not self.adding or self.release is not None)
        )
        self.icon_preview.setVisible(self.adding)
        self.choose_icon_button.setEnabled(not busy and self.icon_target is not None)
        self.icon_choices.setEnabled(not busy and self.icon_target is not None)
        self.update_remove_button()

    def inspection_finished(self, result: TaskResult) -> None:
        self.task = None
        self.set_busy(False)
        if self.closing:
            super().reject()
            return
        self.release = result.releases.get(self.candidate.id) if self.candidate else None
        if self.release is None:
            self.status.setText(result.message)
            return
        self.version.setText(f"v{self.release.version} · Linux {architecture()}")
        self.filename.setText(self.release.name)
        self.checksum.setText("Published SHA-256 available; verified when downloaded")
        self.set_icon_options(result.icons)
        self.status.setText(
            "Ready to add." if result.icons else "Ready to add with the default icon."
        )
        self.add_button.setEnabled(True)
        self.set_busy(False)

    def inspection_failed(self, error: str) -> None:
        self.inspection_finished(TaskResult(error))

    def add_repository(self) -> None:
        if self.task is not None or self.candidate is None or self.release is None:
            return
        app, png = self.candidate, self.selected_icon
        warning = ""
        try:
            self.library.add_repository(app)
            if png is not None:
                try:
                    self.library.set_icon(app, png)
                except (OSError, ValueError) as error:
                    warning = f" Icon could not be saved: {error}"
            self.refresh_list()
            for row in range(self.repo_list.count()):
                if self.repo_list.item(row).data(Qt.ItemDataRole.UserRole) == app.id:
                    self.repo_list.setCurrentRow(row)
                    break
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.add_button.setEnabled(False)
        self.release = None
        self.candidate = None
        self.status.setText(f"Added {app.name}. Close this window to install it.{warning}")
        self.catalog_changed.emit()

    def show_icon(self, path: Path | bytes) -> None:
        image = QPixmap()
        if isinstance(path, bytes):
            image.loadFromData(path, "PNG")
        else:
            image.load(str(path))
        if image.isNull():
            image.load(str(APP_ICON))
        self.icon_preview.setPixmap(
            image.scaled(
                64,
                64,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.detail_icon.setPixmap(self.icon_preview.pixmap())

    def set_icon_options(self, icons: tuple[IconCandidate, ...]) -> None:
        self.icon_options = icons
        self.icon_choices.blockSignals(True)
        self.icon_choices.clear()
        for icon in icons:
            self.icon_choices.addItem(Path(icon.name).name)
            self.icon_choices.setItemData(
                self.icon_choices.count() - 1, icon.name, Qt.ItemDataRole.ToolTipRole
            )
        self.icon_choices.addItem("AppHarbour default")
        self.icon_choices.blockSignals(False)
        self.selected_icon = icons[0].png if icons else None
        self.show_icon(self.selected_icon if self.selected_icon is not None else APP_ICON)

    def select_icon(self, index: int) -> None:
        if self.task is not None or index < 0:
            return
        self.selected_icon = (
            self.icon_options[index].png if index < len(self.icon_options) else None
        )
        self.show_icon(self.selected_icon if self.selected_icon is not None else APP_ICON)
        if self.icon_target is not None and self.candidate is None:
            try:
                self.library.set_icon(self.icon_target, self.selected_icon)
            except (OSError, ValueError) as error:
                self.status.setText(f"Icon could not be saved: {error}")
                return
            self.status.setText(f"Saved icon for {self.icon_target.name}.")
            item = self.repo_list.currentItem()
            if item and item.data(Qt.ItemDataRole.UserRole) == self.icon_target.id:
                item.setIcon(QIcon(self.icon_preview.pixmap()))
            self.catalog_changed.emit()

    def choose_icon(self) -> None:
        if self.task is not None or self.icon_target is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose application icon", "", "Application icons (*.png *.svg)"
        )
        if not path:
            return
        try:
            with Path(path).open("rb") as source:
                png = normalise_icon(source.read(MAX_ICON_BYTES + 1))
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.set_icon_options((IconCandidate(Path(path).name, png),))
        self.select_icon(0)

    def find_existing_icons(self) -> None:
        if self.task is not None or self.icon_target is None or self.candidate is not None:
            return
        app = self.icon_target

        def operation(cancel: Event, progress: Progress) -> TaskResult:
            return TaskResult("Icons inspected.", icons=discover_icons(app, cancel))

        self.task = Task(operation)
        self.task.signals.finished.connect(self.icons_finished)
        self.task.signals.failed.connect(lambda error: self.icons_finished(TaskResult(error)))
        self.set_busy(True)
        self.status.setText(f"Looking for icons for {app.name}…")
        QThreadPool.globalInstance().start(self.task)

    def icons_finished(self, result: TaskResult) -> None:
        self.task = None
        self.set_busy(False)
        if self.closing:
            super().reject()
            return
        if not result.icons:
            self.status.setText("No icon found. Choose an image to replace the current icon.")
            return
        self.set_icon_options(result.icons)
        self.select_icon(0)

    def remove_repository(self) -> None:
        item = self.repo_list.currentItem()
        if self.task is not None or item is None:
            return
        try:
            self.library.remove_repository(str(item.data(Qt.ItemDataRole.UserRole)))
            self.refresh_list()
            self.repo_list.setCurrentRow(0)
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.status.setText("Repository removed. No application files were deleted.")
        self.catalog_changed.emit()

    def reject(self) -> None:
        if self.task is not None:
            self.closing = True
            self.task.cancel.set()
            self.buttons.setEnabled(False)
            self.status.setText("Cancelling inspection…")
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.task is not None:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)
