"""GitHub release selection and verified, cancellable AppImage downloads."""

import contextlib
import hashlib
import json
import os
import platform
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from http.client import HTTPMessage
from pathlib import Path
from threading import Event
from typing import IO, Any
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from appharbour.catalog import App
from appharbour.storage import atomic_json

CACHE_SECONDS = 5 * 60
_RETRY_AFTER: dict[str, float] = {}

MAX_METADATA = 4 * 1024 * 1024
MAX_DOWNLOAD = 4 * 1024 * 1024 * 1024
VERSION = r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?"
Progress = Callable[[str, int, int], None]


class Cancelled(Exception):
    """The user cancelled an operation before activation."""


def check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise Cancelled("Operation cancelled. Your installed version was kept.")


def architecture() -> str:
    machine = platform.machine().lower()
    aliases = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    if machine not in aliases:
        raise ValueError(f"AppImage downloads are not supported on {machine} yet.")
    return aliases[machine]


def stems(app: App) -> tuple[str, ...]:
    if app.asset_stem:
        return (app.asset_stem,)
    former = {"pdfcraft": "printcraft", "solidcraft": "solvecraft"}
    return (app.id, former[app.id]) if app.id in former else (app.id,)


def version_key(version: str | None) -> tuple[int, int, int, int, str]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(.*)", version or "")
    if not match:
        return (0, 0, 0, 0, "")
    suffix = match[4].split("+", 1)[0]
    return (int(match[1]), int(match[2]), int(match[3]), int(not suffix), suffix)


def valid_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc
        in {
            "api.github.com",
            "github.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
            "github-releases.githubusercontent.com",
        }
        and not parsed.username
        and not parsed.password
    )


class GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> Request | None:
        if not valid_url(newurl):
            raise ValueError("GitHub redirected to an untrusted download location.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RateLimited(ValueError):
    def __init__(self, host: str, retry_at: float) -> None:
        self.host = host
        self.retry_at = retry_at
        wait = max(1, int(retry_at - time.time()))
        super().__init__(
            f"GitHub's request limit was reached. Retry in about {wait // 60 + 1} minutes."
        )


def request(url: str, *, method: str = "GET") -> Any:
    if not valid_url(url):
        raise ValueError("Refusing an untrusted download location.")
    host = urlsplit(url).netloc
    if _RETRY_AFTER.get(host, 0) > time.time():
        raise RateLimited(host, _RETRY_AFTER[host])
    req = Request(url, headers={"User-Agent": "AppHarbour/0.1.0"}, method=method)
    try:
        return build_opener(GitHubRedirect()).open(req, timeout=20)
    except HTTPError as error:
        if error.code in (403, 429):
            limited = (
                error.code == 429
                or error.headers.get("X-RateLimit-Remaining") == "0"
                or error.headers.get("Retry-After") is not None
            )
            if not limited:
                limited = b"rate limit" in error.read(4096).lower()
            if limited:
                now = time.time()
                retry_at = now + 60
                with contextlib.suppress(ValueError, TypeError):
                    retry_at = max(retry_at, float(error.headers.get("X-RateLimit-Reset", 0)))
                with contextlib.suppress(ValueError, TypeError):
                    retry_at = max(retry_at, now + float(error.headers.get("Retry-After", 0)))
                retry_at = min(retry_at, now + 24 * 60 * 60)
                _RETRY_AFTER[host] = retry_at
                error.close()
                raise RateLimited(host, retry_at) from error
            error.close()
            raise ValueError("GitHub denied access to this resource (HTTP 403).") from error
        if error.code == 404:
            error.close()
            raise ValueError("No published release was found in this repository.") from error
        raise


def read_metadata(url: str) -> bytes:
    with request(url) as response:
        data: bytes = response.read(MAX_METADATA + 1)
    if len(data) > MAX_METADATA:
        raise ValueError("The release metadata is too large.")
    return data


@dataclass(frozen=True)
class Release:
    version: str
    name: str
    url: str
    size: int
    sha256: str

    def newer_than(self, installed_version: str | None) -> bool:
        return installed_version is not None and version_key(self.version) > version_key(
            installed_version
        )


def asset_names(app: App, version: str, arch: str) -> Iterator[str]:
    for stem in stems(app):
        yield f"{stem}-{version}-linux-{arch}.AppImage"
        yield f"{stem}-linux-{arch}.AppImage"
        yield f"{stem}-{version}-{arch}.AppImage"
        yield f"{stem}-{arch}.AppImage"


def select_release(app: App, data: object, arch: str) -> Release:
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise ValueError("No stable release is available.")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or len(tag) > 128 or not re.fullmatch(rf"v?{VERSION}", tag):
        raise ValueError("The release version is not recognised; expected a tag like v1.2.3.")
    version = tag.removeprefix("v")
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("This release has no downloadable files.")
    for name in asset_names(app, version, arch):
        asset = next((a for a in assets if isinstance(a, dict) and a.get("name") == name), None)
        if asset is None:
            continue
        url = asset.get("browser_download_url")
        prefix = f"https://github.com/{app.repo}/releases/download/"
        if (
            not isinstance(url, str)
            or not url.lower().startswith(prefix.lower())
            or url[len(prefix) :] != f"{quote(tag, safe='')}/{name}"
        ):
            raise ValueError("The release's AppImage URL does not match its repository.")
        size = asset.get("size")
        if type(size) is not int or not 0 < size <= MAX_DOWNLOAD:
            raise ValueError("The AppImage has an invalid download size.")
        digest = asset.get("digest", "")
        sha256 = digest[7:] if isinstance(digest, str) and digest.startswith("sha256:") else ""
        if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
            sha256 = ""
        return Release(version, name, url, size, sha256.lower())
    example = next(asset_names(app, version, arch))
    raise ValueError(
        f"No supported Linux {arch} AppImage filename was found. Expected a name like {example}."
    )


def checksums(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[a-fA-F0-9]{64}", parts[0]):
            continue
        name, digest = parts[1].lstrip("*"), parts[0].lower()
        if name in result and result[name] != digest:
            raise ValueError("The release publishes conflicting SHA-256 checksums.")
        result[name] = digest
    return result


def api_release(app: App, cancel: Event) -> Release:
    check_cancel(cancel)
    data = json.loads(read_metadata(f"https://api.github.com/repos/{app.repo}/releases/latest"))
    release = select_release(app, data, architecture())
    if release.sha256:
        return release
    tag = str(data["tag_name"])
    url = f"https://github.com/{app.repo}/releases/download/{quote(tag, safe='')}/SHA256SUMS.txt"
    check_cancel(cancel)
    digest = checksums(read_metadata(url).decode("utf-8")).get(release.name)
    if digest is None:
        raise ValueError("No published SHA-256 checksum was found for this AppImage.")
    return Release(release.version, release.name, release.url, release.size, digest)


def page_release(app: App, cancel: Event) -> Release:
    """Public release URLs remain usable when the REST API quota is exhausted."""
    check_cancel(cancel)
    with request(f"https://github.com/{app.repo}/releases/latest") as response:
        location = str(response.geturl())
    prefix = f"https://github.com/{app.repo}/releases/tag/"
    if not location.lower().startswith(prefix.lower()):
        raise ValueError("GitHub did not identify a stable release for this repository.")
    tag = unquote(location[len(prefix) :])
    if len(tag) > 128 or not re.fullmatch(rf"v?{VERSION}", tag):
        raise ValueError("GitHub returned an invalid release tag.")
    download_prefix = f"https://github.com/{app.repo}/releases/download/{quote(tag, safe='')}/"
    check_cancel(cancel)
    sums = checksums(read_metadata(download_prefix + "SHA256SUMS.txt").decode("utf-8"))
    name = next(
        (name for name in asset_names(app, tag.removeprefix("v"), architecture()) if name in sums),
        None,
    )
    if name is None:
        raise ValueError(
            f"No supported Linux {architecture()} AppImage filename "
            "with a published checksum was found."
        )
    check_cancel(cancel)
    with request(download_prefix + name, method="HEAD") as response:
        length = response.headers.get("Content-Length", "")
    if not length.isdigit() or len(length) > 12:
        raise ValueError("GitHub did not provide the AppImage's download size.")
    return select_release(
        app,
        {
            "tag_name": tag,
            "assets": [
                {
                    "name": name,
                    "browser_download_url": download_prefix + name,
                    "size": int(length),
                    "digest": "sha256:" + sums[name],
                }
            ],
        },
        architecture(),
    )


def cached_release(app: App, cache_dir: Path) -> Release | None:
    path = cache_dir / f"{app.id}-{architecture()}.json"
    try:
        if path.stat().st_size > MAX_METADATA:
            return None
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return None
        checked_at = data.get("checked_at")
        if (
            not isinstance(checked_at, (float, int))
            or isinstance(checked_at, bool)
            or not 0 <= time.time() - checked_at < CACHE_SECONDS
        ):
            return None
        item = data["release"]
        prefix = f"https://github.com/{app.repo}/releases/download/"
        url = item["url"]
        if not isinstance(url, str) or not url.lower().startswith(prefix.lower()):
            return None
        tag = unquote(url[len(prefix) :].split("/", 1)[0])
        release = select_release(
            app,
            {
                "tag_name": tag,
                "assets": [
                    {
                        "name": item["name"],
                        "browser_download_url": url,
                        "size": item["size"],
                        "digest": "sha256:" + item["sha256"],
                    }
                ],
            },
            architecture(),
        )
        return release if release.sha256 and release.version == item["version"] else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def latest_release(app: App, cancel: Event, cache_dir: Path | None = None) -> Release:
    check_cancel(cancel)
    if cache_dir is not None:
        cached = cached_release(app, cache_dir)
        if cached is not None:
            return cached
        try:
            limits_path = cache_dir / "limits.json"
            if limits_path.stat().st_size <= MAX_METADATA:
                limits = json.loads(limits_path.read_text())
                if isinstance(limits, dict):
                    for host, until in limits.items():
                        if (
                            valid_url(f"https://{host}/")
                            and type(until) in (int, float)
                            and 0 < until - time.time() <= 24 * 60 * 60
                        ):
                            _RETRY_AFTER[host] = max(_RETRY_AFTER.get(host, 0), until)
        except (OSError, ValueError, TypeError):
            pass
    try:
        release = api_release(app, cancel)
    except RateLimited:
        if cache_dir is not None:
            with contextlib.suppress(OSError):
                atomic_json(cache_dir / "limits.json", _RETRY_AFTER)
        release = page_release(app, cancel)
    check_cancel(cancel)
    if cache_dir is not None:
        with contextlib.suppress(OSError):
            atomic_json(
                cache_dir / f"{app.id}-{architecture()}.json",
                {"checked_at": time.time(), "release": asdict(release)},
            )
    return release


def download(release: Release, destination: Path, cancel: Event, progress: Progress) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", release.sha256):
        raise ValueError("A published SHA-256 checksum is required before downloading.")
    digest = hashlib.sha256()
    received = 0
    with request(release.url) as response, destination.open("wb") as output:
        while True:
            check_cancel(cancel)
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > release.size or received > MAX_DOWNLOAD:
                raise ValueError("The download exceeded its published size.")
            digest.update(chunk)
            output.write(chunk)
            progress("Downloading AppImage", received, release.size)
        output.flush()
        os.fsync(output.fileno())
    check_cancel(cancel)
    if received != release.size:
        raise ValueError("The download was incomplete. The installed version was kept.")
    if digest.hexdigest() != release.sha256:
        raise ValueError("SHA-256 verification failed. The installed version was kept.")
