"""AppGlider: a home for Linux AppImages."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

APP_NAME = "AppGlider"
APP_ICON = Path(__file__).parent / "assets" / "icons" / "appglider.png"

try:
    APP_VERSION = version("appglider")
except PackageNotFoundError:
    APP_VERSION = "development"
