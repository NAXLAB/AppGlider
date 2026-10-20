"""A version and its release badge, kept together wherever displayed."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


class VersionLabel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.caption = QLabel()
        self.caption.setObjectName("versionCaption")
        self.caption.hide()
        layout.addWidget(self.caption)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        row.setSpacing(6)
        self.text = QLabel()
        self.text.setObjectName("status")
        self.text.setWordWrap(True)
        self.text.setMinimumWidth(0)
        row.addWidget(self.text)
        self.badge = QLabel()
        self.badge.setObjectName("releaseBadge")
        self.badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.addWidget(self.badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addStretch()

    def set_version(
        self, text: str, prerelease: bool = False, is_latest: bool = False, *, caption: str = ""
    ) -> None:
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
        self.text.setText(text)
        label = "Pre-release" if prerelease else "Latest" if is_latest else ""
        self.badge.setText(label)
        state = "prerelease" if prerelease else "latest"
        if self.badge.property("release") != state:
            self.badge.setProperty("release", state)
            self.badge.style().unpolish(self.badge)
            self.badge.style().polish(self.badge)
        self.badge.setToolTip(
            "A preview version, which may be less reliable."
            if prerelease
            else "GitHub's designated latest stable release."
        )
        self.badge.setVisible(bool(label))
