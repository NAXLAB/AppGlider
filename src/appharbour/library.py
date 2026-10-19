"""Install, update and launch this launcher’s private AppImage library."""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from appharbour import APP_ICON
from appharbour.catalog import APPS, App, custom_app, load_apps
from appharbour.icons import MAX_ICON_BYTES, normalise_icon
from appharbour.releases import (
    VERSION,
    Progress,
    Release,
    architecture,
    check_cancel,
    download,
)
from appharbour.storage import atomic_bytes, atomic_json


def desktop_exec(arguments: list[str]) -> str:
    quoted = []
    for argument in arguments:
        if any(ord(c) < 32 or ord(c) == 127 for c in argument):
            raise ValueError("The shortcut command contains control characters.")
        escaped = argument.replace("\\", "\\\\\\\\").replace("%", "%%")
        for char in ('"', "`", "$"):
            escaped = escaped.replace(char, "\\\\" + char)
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


@dataclass(frozen=True)
class Installed:
    path: Path
    version: str | None
    managed: bool = False
    previous: Path | None = None


def check_appimage(path: Path) -> None:
    with path.open("rb") as source:
        head = source.read(20)
    if head[:4] != b"\x7fELF" or head[8:11] != b"AI\x02" or len(head) < 20:
        raise ValueError("The selected file is not a type 2 AppImage.")
    machine = int.from_bytes(head[18:20], "little" if head[5] == 1 else "big")
    expected = {"x86_64": 62, "aarch64": 183}[architecture()]
    if machine != expected:
        raise ValueError("This AppImage was built for a different CPU architecture.")


def running(path: Path) -> bool:
    target = os.fsencode(path.resolve())
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if (process / "exe").resolve() == path.resolve():
                return True
            with (process / "environ").open("rb") as source:
                environment = source.read(1024 * 1024).split(b"\0")
            if b"APPIMAGE=" + target in environment:
                return True
        except OSError:
            continue
    return False


class Library:
    def __init__(
        self,
        data_dir: Path,
        apps_dir: Path,
        desktop_dir: Path | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.apps_dir = apps_dir
        home = Path.home()
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))
        self.desktop_dir = desktop_dir if desktop_dir is not None else xdg_data / "applications"
        self.processes: dict[str, subprocess.Popen[bytes]] = {}

    @property
    def apps(self) -> tuple[App, ...]:
        return load_apps(self.data_dir / "repositories.json")

    def icon_path(self, app: App) -> Path:
        path = self.data_dir / "icons" / f"{app.id}.png"
        if app.asset_stem and not path.parent.is_symlink() and not path.is_symlink():
            try:
                if path.stat().st_size <= MAX_ICON_BYTES:
                    with path.open("rb") as source:
                        if source.read(8) == b"\x89PNG\r\n\x1a\n":
                            return path
            except OSError:
                pass
        return app.icon

    def set_icon(self, app: App, data: bytes | None) -> None:
        png = normalise_icon(data if data is not None else APP_ICON.read_bytes())
        with self._lock():
            if app not in self.apps[len(APPS) :]:
                raise ValueError("Only added repositories can have a custom icon.")
            directory = self.data_dir / "icons"
            if directory.is_symlink():
                raise ValueError("The icon folder is a symbolic link; it was left untouched.")
            atomic_bytes(directory / f"{app.id}.png", png)

    def add_repository(self, app: App) -> None:
        if app != custom_app(app.repo, app.name):
            raise ValueError("Invalid custom repository details.")
        with self._lock():
            apps = self.apps
            if any(known.repo.casefold() == app.repo.casefold() for known in apps):
                raise ValueError("This repository is already in the catalog.")
            self.records()
            self._save_repositories((*apps[len(APPS) :], app))

    def remove_repository(self, app_id: str) -> None:
        with self._lock():
            apps = self.apps
            if app_id not in {app.id for app in apps[len(APPS) :]}:
                raise ValueError("Only custom repositories can be removed.")
            if app_id in self.records():
                raise ValueError(
                    "Uninstall this application's managed copy before removing its repository."
                )
            self._save_repositories(tuple(app for app in apps[len(APPS) :] if app.id != app_id))
            with contextlib.suppress(OSError):
                icon = self.data_dir / "icons" / f"{app_id}.png"
                if not icon.parent.is_symlink():
                    icon.unlink(missing_ok=True)

    def _save_repositories(self, apps: tuple[App, ...]) -> None:
        if len(apps) > 100:
            raise ValueError("The catalog supports up to 100 custom repositories.")
        atomic_json(
            self.data_dir / "repositories.json",
            [{"repo": app.repo, "name": app.name} for app in apps],
        )

    def records(self) -> dict[str, Installed]:
        path = self.data_dir / "library.json"
        if not path.exists():
            return {}
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("The library file is too large.")
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("Invalid library records.")
            known_ids = {app.id for app in self.apps}
            result = {}
            for app_id, item in data.items():
                if app_id not in known_ids or not isinstance(item, dict):
                    raise ValueError("Unrecognised library entry.")
                location, version = item.get("path"), item.get("version")
                previous, managed = item.get("previous"), item.get("managed")
                if (
                    not isinstance(location, str)
                    or not Path(location).is_absolute()
                    or type(managed) is not bool
                    or (
                        version is not None
                        and (
                            not isinstance(version, str)
                            or len(version) > 128
                            or not re.fullmatch(VERSION, version)
                        )
                    )
                    or (
                        previous is not None
                        and (not isinstance(previous, str) or not Path(previous).is_absolute())
                    )
                ):
                    raise ValueError("Invalid installation record.")
                result[app_id] = Installed(
                    Path(location),
                    version,
                    managed,
                    Path(previous) if previous is not None else None,
                )
            return result
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read {path}. The file was preserved: {error}") from error

    def _save(self, records: dict[str, Installed]) -> None:
        atomic_json(
            self.data_dir / "library.json",
            {
                app_id: {
                    "path": str(item.path),
                    "version": item.version,
                    "managed": item.managed,
                    "previous": str(item.previous) if item.previous else None,
                }
                for app_id, item in records.items()
            },
        )

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with (self.data_dir / "library.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError(
                    "Another launcher is changing this library. Try again shortly."
                ) from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def scan(self) -> dict[str, Installed]:
        return {
            key: value
            for key, value in self.records().items()
            if value.managed and value.path.is_file()
        }

    def launch(self, app: App, installed: Installed) -> Path:
        if not installed.managed:
            raise ValueError("Only apps installed by AppHarbour can be launched here.")
        self._owned(app, installed.path)
        check_appimage(installed.path)
        if not os.access(installed.path, os.X_OK):
            raise ValueError("Make this AppImage executable in file properties, then try again.")
        environment = os.environ.copy()
        appdir = environment.get("APPDIR")
        if appdir:
            for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
                value = environment.get(name)
                if value and Path(value).is_relative_to(Path(appdir)):
                    environment.pop(name)
        for name in (
            "APPIMAGE",
            "APPDIR",
            "ARGV0",
            "OWD",
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "PYTHONHOME",
            "PYTHONPATH",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            "QML2_IMPORT_PATH",
            "QML_IMPORT_PATH",
        ):
            environment.pop(name, None)
        log = self.data_dir / "logs" / f"{app.id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as output:
            self.processes = {
                key: proc for key, proc in self.processes.items() if proc.poll() is None
            }
            previous = self.processes.get(app.id)
            if previous is not None:
                raise ValueError(f"{app.name} is already running from this launcher.")
            process = subprocess.Popen(
                [str(installed.path)],
                cwd=installed.path.parent,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.processes[app.id] = process
        return log

    def _marker(self, app: App) -> str:
        return f"AppHarbour\n{app.id}\n{app.repo}\n"

    def _owned(self, app: App, path: Path) -> Path:
        root = path.parent.parent
        if (
            root.name != app.id
            or root.is_symlink()
            or path.parent.is_symlink()
            or path.is_symlink()
            or not re.fullmatch(VERSION, path.parent.name)
            or path.suffix.lower() != ".appimage"
            or (root / ".appharbour").read_text() != self._marker(app)
        ):
            raise ValueError("This path is not owned by AppHarbour; it was left untouched.")
        return root

    def _remove_file(self, app: App, path: Path) -> None:
        self._owned(app, path)
        if running(path):
            raise ValueError(f"{app.name} is running. Close it before removing this version.")
        path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            path.parent.rmdir()

    def install(self, app: App, release: Release, cancel: Event, progress: Progress) -> Installed:
        with self._lock():
            if app not in self.apps:
                raise ValueError("Add this repository to the catalog before installing its app.")
            records = self.records()
            old = records.get(app.id)
            if old and not old.managed:
                # Ignore legacy links; a successful install replaces only their registry entry.
                old = None
            root = self._owned(app, old.path) if old else self.apps_dir.resolve() / app.id
            if root.is_symlink():
                raise ValueError("The installation folder is a symbolic link.")
            if root.exists():
                if (root / ".appharbour").read_text() != self._marker(app):
                    raise ValueError("The installation folder belongs to another application.")
            else:
                root.mkdir(parents=True)
                (root / ".appharbour").write_text(self._marker(app))
            if (
                not re.fullmatch(VERSION, release.version)
                or Path(release.name).name != release.name
            ):
                raise ValueError("The release has an unsafe filename or version.")
            version_dir = root / release.version
            if version_dir.is_symlink():
                raise ValueError("The installation folder is a symbolic link.")
            version_dir.mkdir(exist_ok=True)
            destination = version_dir / release.name
            if destination.is_symlink():
                raise ValueError("The installed file is a symbolic link.")
            created = not destination.exists()
            if not created:
                with destination.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                if digest != release.sha256 or destination.stat().st_size != release.size:
                    raise ValueError("The existing version has a different checksum; it was kept.")
                check_appimage(destination)
            else:
                fd, temporary = tempfile.mkstemp(prefix=".download-", dir=root)
                os.close(fd)
                stage = Path(temporary)
                try:
                    download(release, stage, cancel, progress)
                    check_appimage(stage)
                    check_cancel(cancel)
                    stage.chmod(0o755)
                    os.replace(stage, destination)
                finally:
                    stage.unlink(missing_ok=True)
            check_cancel(cancel)
            if old and old.path == destination:
                return old
            installed = Installed(destination, release.version, True, old.path if old else None)
            records[app.id] = installed
            try:
                self._save(records)
            except OSError:
                if created:
                    destination.unlink()
                raise
            if old and old.previous and old.previous != destination:
                with contextlib.suppress(OSError, ValueError):
                    self._remove_file(app, old.previous)
            try:
                self._shortcut(app, installed.path)
            except OSError as error:
                raise ValueError(
                    f"{app.name} was installed, but its shortcut could not be saved: {error}"
                ) from error
            return installed

    def rollback(self, app: App) -> None:
        with self._lock():
            records = self.records()
            old = records.get(app.id)
            if not old or not old.managed or not old.previous:
                raise ValueError("There is no previous managed version to restore.")
            self._owned(app, old.path)
            self._owned(app, old.previous)
            check_appimage(old.previous)
            records[app.id] = Installed(old.previous, old.previous.parent.name, True, old.path)
            self._save(records)
            self._shortcut(app, old.previous)

    def remove(self, app: App) -> None:
        with self._lock():
            records = self.records()
            old = records.get(app.id)
            if not old or not old.managed:
                raise ValueError("Only applications installed by this launcher can be removed.")
            paths = [p for p in (old.path, old.previous) if p is not None]
            for path in paths:
                self._owned(app, path)
                if running(path):
                    raise ValueError(f"Close {app.name} before removing it.")
            for path in paths:
                self._remove_file(app, path)
            del records[app.id]
            self._save(records)
            shortcut = self.desktop_dir / f"appharbour-{app.id}.desktop"
            if shortcut.is_file() and "X-AppHarbour=true" in shortcut.read_text().splitlines():
                shortcut.unlink()

    def create_shortcut(self, app: App) -> None:
        with self._lock():
            installed = self.records().get(app.id)
            if not installed or not installed.managed or not installed.path.is_file():
                raise ValueError("Install this app with AppHarbour first.")
            self._owned(app, installed.path)
            self._shortcut(app, installed.path)

    def create_launcher_shortcut(self, arguments: list[str]) -> Path:
        command = desktop_exec(arguments)
        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        shortcut = self.desktop_dir / "appharbour.desktop"
        if shortcut.is_symlink() or (
            shortcut.exists() and "X-AppHarbour=true" not in shortcut.read_text().splitlines()
        ):
            raise OSError("The existing AppHarbour shortcut belongs to another application.")
        icon = self.data_dir / "icons" / "appharbour.png"
        if icon.parent.is_symlink() or icon.is_symlink():
            raise OSError("The shortcut icon path is a symbolic link; it was left untouched.")
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(APP_ICON, icon)
        shortcut.write_text(
            "[Desktop Entry]\nType=Application\nName=AppHarbour\n"
            "Comment=Install, update and launch Linux AppImages\n"
            f"Exec={command}\nIcon={icon}\nTerminal=false\n"
            "Categories=Utility;\nX-AppHarbour=true\n"
        )
        return shortcut

    def _shortcut(self, app: App, path: Path) -> None:
        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        shortcut = self.desktop_dir / f"appharbour-{app.id}.desktop"
        if shortcut.exists() and "X-AppHarbour=true" not in shortcut.read_text().splitlines():
            raise OSError("An existing desktop shortcut is not owned by this launcher.")
        icon = self.data_dir / "icons" / f"{app.id}.png"
        if icon.parent.is_symlink() or icon.is_symlink():
            raise OSError("The shortcut icon path is a symbolic link; it was left untouched.")
        icon.parent.mkdir(parents=True, exist_ok=True)
        source = self.icon_path(app)
        if source.resolve() != icon.resolve():
            shutil.copyfile(source, icon)
        command = desktop_exec([str(path)])
        shortcut.write_text(
            f"[Desktop Entry]\nType=Application\nName={app.name} (AppHarbour)\n"
            f"Exec={command} %F\nIcon={icon}\nTerminal=false\n"
            "Categories=Graphics;\nX-AppHarbour=true\n"
        )
