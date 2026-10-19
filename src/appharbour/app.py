"""Desktop entry point."""

import json
import os
import ssl
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from appharbour import APP_ICON, APP_NAME
from appharbour.window import LauncherWindow


def launcher_command() -> list[str]:
    image, directory = os.environ.get("APPIMAGE"), os.environ.get("APPDIR")
    if image and directory and Path(sys.prefix).resolve().is_relative_to(Path(directory).resolve()):
        return [str(Path(image).absolute())]
    return [sys.executable, "-O", "-m", "appharbour"]


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("appharbour")
    app.setWindowIcon(QIcon(str(APP_ICON)))
    app.setStyle("Fusion")
    window = LauncherWindow()
    try:
        window.library.create_launcher_shortcut(launcher_command())
    except (OSError, ValueError) as error:
        window.footer.setText(f"Could not create the AppHarbour menu shortcut: {error}")
    window.show()
    if "--smoke-test" in sys.argv:

        def report() -> None:
            print(
                json.dumps(
                    {
                        "app": APP_NAME,
                        "python": sys.version.split()[0],
                        "prefix": sys.prefix,
                        "appdir": os.environ.get("APPDIR"),
                        "installed": len(window.installed),
                        "catalog": len(window.apps),
                        "icon": not window.windowIcon().isNull(),
                        "https_certificates": ssl.create_default_context().cert_store_stats()[
                            "x509_ca"
                        ],
                        "command": launcher_command(),
                        "data": str(window.library.data_dir),
                    }
                ),
                flush=True,
            )
            app.quit()

        QTimer.singleShot(200, report)
    return app.exec()
