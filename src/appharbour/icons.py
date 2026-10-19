"""Discover bounded repository artwork and turn it into a local PNG icon."""

import base64
import configparser
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import Event

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

from appharbour import APP_ICON
from appharbour.catalog import App
from appharbour.releases import check_cancel, read_metadata

MAX_ICON_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class IconCandidate:
    name: str
    png: bytes


def icon_pixmap(path: Path, size: int) -> QPixmap:
    # QImage avoids QPixmap's file cache retaining an icon replaced within the same second.
    image = QImage(str(path))
    if image.isNull():
        image = QImage(str(APP_ICON))
    return QPixmap.fromImage(
        image.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )


def normalise_icon(data: bytes) -> bytes:
    if not data or len(data) > MAX_ICON_BYTES:
        raise ValueError("Choose a PNG or SVG icon smaller than 2 MiB.")
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    kind = reader.format().data()
    if kind not in {b"png", b"svg"}:
        raise ValueError("The icon must be a valid PNG or SVG image.")
    if kind == b"svg":
        # Rasterise self-contained artwork; never resolve links into local files or the network.
        try:
            text_svg = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("SVG icons must use UTF-8 encoding.") from error
        if "<!DOCTYPE" in text_svg.upper() or "<!ENTITY" in text_svg.upper():
            raise ValueError("SVG icons must not contain document types or entities.")
        try:
            document = ET.fromstring(text_svg)
        except ET.ParseError as error:
            raise ValueError("The SVG icon is malformed.") from error
        for element in document.iter():
            for key, value in element.attrib.items():
                if key.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                    raise ValueError("SVG icons must not reference external images or files.")
            text = " ".join([*element.attrib.values(), element.text or ""])
            if "@import" in text.lower() or any(
                not value.strip(" \t\r\n\"'").startswith("#")
                for value in re.findall(r"url\s*\((.*?)\)", text, re.I | re.S)
            ):
                raise ValueError("SVG icons must not load external resources.")
    size = reader.size()
    if not size.isValid() or max(size.width(), size.height()) > 4096:
        raise ValueError("Icon dimensions must be between 1 and 4096 pixels.")
    reader.setScaledSize(size.scaled(QSize(512, 512), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError("The icon image could not be read.")
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    # PySide6's runtime requires str here although its stub declares bytes.
    if not image.save(output, "PNG"):  # type: ignore[call-overload]
        raise ValueError("The icon could not be converted to PNG.")
    return bytes(output.data().data())


def discover_icons(app: App, cancel: Event) -> tuple[IconCandidate, ...]:
    check_cancel(cancel)
    prefix = f"https://api.github.com/repos/{app.repo}"
    tree = json.loads(read_metadata(prefix + "/git/trees/HEAD?recursive=1"))
    if (
        not isinstance(tree, dict)
        or tree.get("truncated")
        or not isinstance(tree.get("tree"), list)
    ):
        raise ValueError("Could not inspect the repository's icon files. Choose an image instead.")
    files: dict[str, str] = {}
    for item in tree["tree"]:
        if not isinstance(item, dict):
            continue
        path, sha, size = item.get("path"), item.get("sha"), item.get("size")
        if (
            isinstance(path, str)
            and len(path) <= 512
            and not any(ord(c) < 32 or ord(c) == 127 for c in path)
            and not any(part in {"", ".", ".."} for part in path.split("/"))
            and isinstance(sha, str)
            and re.fullmatch(r"[a-f0-9]{40,64}", sha)
            and type(size) is int
            and 0 < size <= MAX_ICON_BYTES
            and item.get("type") == "blob"
            and item.get("mode") in {"100644", "100755"}
        ):
            files[path] = sha

    def read_blob(path: str) -> bytes:
        check_cancel(cancel)
        blob = json.loads(read_metadata(prefix + "/git/blobs/" + files[path]))
        if not isinstance(blob, dict) or blob.get("encoding") != "base64":
            raise ValueError("GitHub did not return a readable icon file.")
        content = blob.get("content")
        if not isinstance(content, str):
            raise ValueError("GitHub returned invalid icon content.")
        data = base64.b64decode("".join(content.split()), validate=True)
        if len(data) > MAX_ICON_BYTES:
            raise ValueError("The repository icon is too large.")
        check_cancel(cancel)
        return data

    declared: set[str] = set()
    desktops = sorted(path for path in files if path.endswith(".desktop"))
    for path in desktops[:2]:
        try:
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read_string(read_blob(path).decode("utf-8"))
            declared.add(PurePosixPath(config.get("Desktop Entry", "Icon")).stem.lower())
        except (OSError, ValueError, configparser.Error):
            continue

    def rank(path: str) -> tuple[int, int, int, int, str]:
        lower = path.lower()
        stem = PurePosixPath(lower).stem
        resolution = max((int(n) for n in re.findall(r"(?:^|/)(\d+)x\d+(?:/|$)", lower)), default=0)
        return (
            int(stem not in declared),
            int("linux" not in lower and "hicolor" not in lower),
            int(PurePosixPath(lower).suffix != ".png"),
            -min(resolution, 512),
            lower,
        )

    candidates = [
        path
        for path in files
        if PurePosixPath(path).suffix.lower() in {".png", ".svg"}
        and not any(
            word in path.lower()
            for word in (
                "screenshot",
                "banner",
                "wordmark",
                "node_modules",
                "vendor/",
                "macos",
                "darwin",
                "windows",
                "win32",
            )
        )
        and (
            PurePosixPath(path.lower()).stem in declared
            or any(word in path.lower() for word in ("icon", "logo", "brand/"))
        )
    ]
    result: list[IconCandidate] = []
    seen: set[str] = set()
    for path in sorted(candidates, key=rank):
        stem = PurePosixPath(path).stem.lower()
        if stem in seen:
            continue
        if len(seen) >= 8:
            break
        seen.add(stem)
        try:
            png = normalise_icon(read_blob(path))
        except (OSError, ValueError):
            continue
        result.append(IconCandidate(path, png))
        if len(result) == 4:
            break
    check_cancel(cancel)
    return tuple(result)
