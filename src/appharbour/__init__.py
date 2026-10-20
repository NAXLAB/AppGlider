"""AppHarbour: a home for Linux AppImages."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

APP_NAME = "AppHarbour"
APP_ICON = Path(__file__).parent / "assets" / "icons" / "appharbour.png"

try:
    APP_VERSION = version("appharbour")
except PackageNotFoundError:
    APP_VERSION = "development"
