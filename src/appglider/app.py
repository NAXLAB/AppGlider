"""Desktop entry point."""

import json
import os
import ssl
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QComboBox

from appglider import APP_ICON, APP_NAME, APP_VERSION
from appglider.instance import SingleInstance
from appglider.window import LauncherWindow


def launcher_command() -> list[str]:
    image, directory = os.environ.get("APPIMAGE"), os.environ.get("APPDIR")
    if image and directory and Path(sys.prefix).resolve().is_relative_to(Path(directory).resolve()):
        return [str(Path(image).absolute())]
    return [sys.executable, "-O", "-m", "appglider"]


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("appglider")
    app.setWindowIcon(QIcon(str(APP_ICON)))
    app.setStyle("Fusion")
    smoke = "--smoke-test" in sys.argv
    instance = SingleInstance(
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
        / "appglider"
    )
    if not smoke and not instance.acquire():
        return 0
    window = LauncherWindow()
    instance.activated.connect(window.show_launcher)
    command = launcher_command()
    try:
        window.library.refresh_launcher_shortcut(command, packaged=len(command) == 1)
    except (OSError, ValueError) as error:
        window.footer.setText(f"Could not create the AppGlider menu shortcut: {error}")
    window.show()
    if smoke:

        def report() -> None:
            print(
                json.dumps(
                    {
                        "app": APP_NAME,
                        "version": app.applicationVersion(),
                        "header_version": window.version_button.text(),
                        "refresh_action": window.check_button.text(),
                        "release_preferences": sum(
                            card.preferences_action.isEnabled() for card in window.cards.values()
                        ),
                        "card_dropdowns": sum(
                            len(card.findChildren(QComboBox)) for card in window.cards.values()
                        ),
                        "python": sys.version.split()[0],
                        "prefix": sys.prefix,
                        "appdir": os.environ.get("APPDIR"),
                        "installed": len(window.installed),
                        "catalog": len(window.apps),
                        "icon": not window.windowIcon().isNull(),
                        "https_certificates": ssl.create_default_context().cert_store_stats()[
                            "x509_ca"
                        ],
                        "command": command,
                        "data": str(window.library.data_dir),
                    }
                ),
                flush=True,
            )
            app.quit()

        QTimer.singleShot(200, report)
    else:
        QTimer.singleShot(0, window.refresh_releases)
    status = app.exec()
    instance.release()
    return status
