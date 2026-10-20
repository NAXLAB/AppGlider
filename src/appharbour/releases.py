"""GitHub release selection and verified, cancellable AppImage downloads."""

import contextlib
import hashlib
import json
import os
import platform
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from html.parser import HTMLParser
from http.client import HTTPMessage
from pathlib import Path
from threading import Event
from typing import IO, Any
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from appharbour.catalog import App
from appharbour.storage import atomic_json

CACHE_SECONDS = 5 * 60
_RETRY_AFTER: dict[str, float] = {}

MAX_METADATA = 4 * 1024 * 1024
MAX_DOWNLOAD = 4 * 1024 * 1024 * 1024
VERSION = r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?"
Progress = Callable[[str, int, int], None]
ASSET_ARCHITECTURES = {
    "x86_64": ("x86_64", "x86-64", "x64", "amd64"),
    "aarch64": ("aarch64", "arm64"),
    "x86": ("x86", "i386", "i486", "i586", "i686", "ia32"),
    "arm": ("arm", "armhf", "armel", "armv6l", "armv7", "armv7l", "armv7hf"),
    "riscv64": ("riscv64",),
    "ppc64": ("ppc64", "ppc64le"),
    "s390x": ("s390x",),
}
ARCH_LABELS = {alias: arch for arch, aliases in ASSET_ARCHITECTURES.items() for alias in aliases}
ARCH_PATTERN = re.compile(
    r"(?<![a-z0-9])("
    + "|".join(map(re.escape, sorted(ARCH_LABELS, key=len, reverse=True)))
    + r")(?![a-z0-9])",
    re.I,
)


class Cancelled(Exception):
    """The user cancelled an operation before activation."""


class MissingResource(ValueError):
    """A GitHub resource returned HTTP 404."""


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
    req = Request(url, headers={"User-Agent": "AppHarbour/0.1.2"}, method=method)
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
            raise MissingResource("No published release was found in this repository.") from error
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


def preferred_asset_names(app: App, version: str, arch: str) -> Iterator[str]:
    for stem in stems(app):
        for filename_arch in ASSET_ARCHITECTURES.get(arch, (arch,)):
            yield f"{stem}-{version}-linux-{filename_arch}.AppImage"
            yield f"{stem}-linux-{filename_arch}.AppImage"
            yield f"{stem}-{version}-{filename_arch}.AppImage"
            yield f"{stem}-{filename_arch}.AppImage"


def select_asset(app: App, assets: list[Any], version: str, arch: str) -> dict[str, Any]:
    labelled = []
    unlabelled = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if not isinstance(name, str) or not name.casefold().endswith(".appimage"):
            continue
        if len(name) > 512 or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in name):
            raise ValueError("The release has an invalid AppImage filename.")
        architectures = {ARCH_LABELS[m[0].lower()] for m in ARCH_PATTERN.finditer(name)}
        if architectures == {arch}:
            labelled.append(asset)
        elif not architectures:
            unlabelled.append(asset)
    candidates = labelled or unlabelled
    if not candidates:
        raise ValueError(f"No supported {arch} AppImage filename was found in this release.")
    for expected in preferred_asset_names(app, version, arch):
        preferred = [a for a in candidates if a["name"].casefold() == expected.casefold()]
        if preferred:
            candidates = preferred
            break
    if len(candidates) != 1:
        names = ", ".join(a["name"] for a in candidates[:5])
        raise ValueError(f"Multiple AppImages match {arch}; cannot choose safely: {names}")
    return candidates[0]


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
    asset = select_asset(app, assets, version, arch)
    name = asset["name"]
    url = asset.get("browser_download_url")
    prefix = f"https://github.com/{app.repo}/releases/download/"
    if (
        not isinstance(url, str)
        or not url.lower().startswith(prefix.lower())
        or unquote(url[len(prefix) :]) != f"{tag}/{name}"
        or urlsplit(url).query
        or urlsplit(url).fragment
        or any(c.isspace() for c in url)
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


def with_checksum(app: App, tag: str, release: Release, cancel: Event) -> Release:
    if release.sha256:
        return release
    url = f"https://github.com/{app.repo}/releases/download/{quote(tag, safe='')}/SHA256SUMS.txt"
    check_cancel(cancel)
    try:
        manifest = read_metadata(url).decode("utf-8")
    except MissingResource as error:
        raise ValueError(
            "No GitHub SHA-256 digest or SHA256SUMS.txt is available for this AppImage."
        ) from error
    digest = checksums(manifest).get(release.name)
    if digest is None:
        raise ValueError("No published SHA-256 checksum was found for this AppImage.")
    return replace(release, sha256=digest)


def api_release(app: App, cancel: Event) -> Release:
    check_cancel(cancel)
    data = json.loads(read_metadata(f"https://api.github.com/repos/{app.repo}/releases/latest"))
    release = select_release(app, data, architecture())
    return with_checksum(app, str(data["tag_name"]), release, cancel)


class ReleaseAssetsParser(HTMLParser):
    """Read GitHub's asset links and filename-labelled digest controls."""

    def __init__(self, download_prefix: str) -> None:
        super().__init__()
        self.download_prefix = download_prefix
        self.assets: dict[str, dict[str, Any]] = {}
        self.digests: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a":
            url = urljoin("https://github.com", attributes.get("href") or "")
            if url.lower().startswith(self.download_prefix.lower()):
                name = unquote(url[len(self.download_prefix) :])
                if name in self.assets and self.assets[name]["browser_download_url"] != url:
                    raise ValueError("GitHub returned conflicting asset download links.")
                # GitHub displays rounded sizes; HEAD supplies the exact size after selection.
                self.assets[name] = {"name": name, "browser_download_url": url, "size": 1}
        if tag == "clipboard-copy":
            label = attributes.get("aria-label") or ""
            prefix = "Copy to clipboard digest for "
            digest = attributes.get("value") or ""
            if label.startswith(prefix) and re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
                name = label[len(prefix) :]
                if name in self.digests and self.digests[name] != digest.lower():
                    raise ValueError("GitHub returned conflicting SHA-256 digests for an asset.")
                self.digests[name] = digest.lower()


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
    parser = ReleaseAssetsParser(download_prefix)
    parser.feed(
        read_metadata(
            f"https://github.com/{app.repo}/releases/expanded_assets/{quote(tag, safe='')}"
        ).decode("utf-8")
    )
    parser.close()
    if not parser.assets:
        raise ValueError("Could not read GitHub's public asset listing; retry discovery later.")
    assets = [
        {**asset, "digest": parser.digests.get(name, "")} for name, asset in parser.assets.items()
    ]
    release = select_release(app, {"tag_name": tag, "assets": assets}, architecture())
    release = with_checksum(app, tag, release, cancel)
    check_cancel(cancel)
    with request(release.url, method="HEAD") as response:
        length = response.headers.get("Content-Length", "")
    if not length.isdigit() or len(length) > 12:
        raise ValueError("GitHub did not provide the AppImage's download size.")
    size = int(length)
    if not 0 < size <= MAX_DOWNLOAD:
        raise ValueError("The AppImage has an invalid download size.")
    return replace(release, size=size)


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
