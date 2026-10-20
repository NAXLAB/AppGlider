"""GitHub release selection and verified, cancellable AppImage downloads."""

import contextlib
import hashlib
import json
import os
import platform
import re
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from html.parser import HTMLParser
from http.client import HTTPException, HTTPMessage
from pathlib import Path
from threading import Event
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from appharbour import APP_VERSION, github_status
from appharbour.catalog import App
from appharbour.storage import atomic_json

CACHE_SECONDS = 5 * 60
RELEASE_PAGES = 3
RETRY_DELAYS = (0.5, 1.5)
RETRY_STATUSES = (500, 502, 503, 504)
_RETRY_AFTER: dict[str, float] = {}

MAX_METADATA = 4 * 1024 * 1024
MAX_DOWNLOAD = 4 * 1024 * 1024 * 1024
VERSION = r"[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?"
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


def release_version(tag: object) -> str | None:
    """Extract a comparable version without changing the tag used in GitHub URLs."""
    if not isinstance(tag, str) or len(tag) > 128:
        return None
    match = re.fullmatch(rf"(?:[A-Za-z][A-Za-z0-9_-]*[-_/])?[vV]?({VERSION})", tag)
    return match[1] if match else None


def version_key(
    version: str | None,
) -> tuple[int, int, int, int, int, tuple[tuple[int, int, str], ...]]:
    match = re.fullmatch(
        r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([^+]+))?(?:\+.*)?", version or ""
    )
    if not match:
        return (0, 0, 0, 0, 0, ())
    suffix = match[5] or ""
    identifiers = tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part) for part in suffix.split(".") if part
    )
    return (
        int(match[1]),
        int(match[2]),
        int(match[3] or 0),
        int(match[4] or 0),
        int(not suffix),
        identifiers,
    )


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


UNREACHABLE = "Could not reach GitHub. Check your connection and try again."


def timed_out(error: Exception) -> bool:
    reason = error.reason if isinstance(error, URLError) else error
    return isinstance(reason, TimeoutError)


def request(url: str, *, method: str = "GET") -> Any:
    if not valid_url(url):
        raise ValueError("Refusing an untrusted download location.")
    host = urlsplit(url).netloc
    if _RETRY_AFTER.get(host, 0) > time.time():
        raise RateLimited(host, _RETRY_AFTER[host])
    req = Request(url, headers={"User-Agent": f"AppHarbour/{APP_VERSION}"}, method=method)
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return _open(req, host)
        except HTTPError as error:
            if error.code in RETRY_STATUSES:
                error.close()
                if attempt < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                raise ValueError(
                    f"GitHub is temporarily unavailable (HTTP {error.code}). Try again shortly."
                ) from error
            raise
        except (URLError, OSError, HTTPException) as error:
            if timed_out(error) and attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])
                continue
            raise ValueError(UNREACHABLE) from error
    raise AssertionError("unreachable")  # pragma: no cover


def _open(req: Request, host: str) -> Any:
    try:
        response = build_opener(GitHubRedirect()).open(req, timeout=20)
        if host == "api.github.com":
            github_status.observe_headers(dict(response.headers.items()))
        return response
    except HTTPError as error:
        if host == "api.github.com":
            github_status.observe_headers(dict(error.headers.items()))
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
    try:
        with request(url) as response:
            data: bytes = response.read(MAX_METADATA + 1)
    except HTTPError:
        raise
    except (OSError, HTTPException) as error:
        raise ValueError(UNREACHABLE) from error
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
    prerelease: bool = False
    is_latest: bool = True

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


def asset_variant(name: str, version: str, *, keep_timestamp: bool = False) -> str:
    # Keep distribution/edition text; only the release version and build date may change.
    pattern = name.replace(version, "{version}")
    if not keep_timestamp:
        pattern = re.sub(r"(?<=[_.-])\d{8}(?:\d{6})?(?=[_.-]|$)", "{timestamp}", pattern)
    return pattern


def variant_label(pattern: str) -> str:
    label = re.sub(r"[_.-]*v?\{version\}|[_.-]*\{timestamp\}|\.appimage$", "", pattern, flags=re.I)
    return re.sub(r"ubuntu(?=\d)", "Ubuntu ", label, flags=re.I).replace("_", " ").strip("- .")


def build_cache_suffix(app: App) -> str:
    return (
        "-build-" + hashlib.sha256(app.build_variant.encode()).hexdigest()[:16]
        if app.build_variant
        else ""
    )


class BuildChoiceRequired(ValueError):
    def __init__(
        self,
        app: App,
        assets: list[dict[str, Any]],
        version: str,
        arch: str,
        *,
        missing: bool = False,
    ) -> None:
        self.app_id = app.id
        self.missing = missing
        patterns = [asset_variant(asset["name"], version) for asset in assets]
        counts = Counter(pattern.casefold() for pattern in patterns)
        self.choices = tuple(
            (
                asset_variant(
                    asset["name"], version, keep_timestamp=counts[pattern.casefold()] > 1
                ),
                asset["name"],
            )
            for asset, pattern in zip(assets, patterns, strict=True)
        )
        names = ", ".join(asset["name"] for asset in assets[:5])
        super().__init__(
            "The saved build is unavailable. Choose a build again: " + names
            if missing
            else f"Multiple AppImages match {arch}; cannot choose safely: {names}"
        )


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
    if app.build_variant:
        variants = [
            {
                asset_variant(asset["name"], version),
                asset_variant(asset["name"], version, keep_timestamp=True),
            }
            for asset in candidates
        ]
        selected = [
            asset
            for asset, patterns in zip(candidates, variants, strict=True)
            if app.build_variant in patterns
        ]
        if not selected:
            selected = [
                asset
                for asset, patterns in zip(candidates, variants, strict=True)
                if app.build_variant.casefold() in {pattern.casefold() for pattern in patterns}
            ]
        if len(selected) == 1:
            return selected[0]
        raise BuildChoiceRequired(app, candidates, version, arch, missing=not selected)
    for expected in preferred_asset_names(app, version, arch):
        preferred = [a for a in candidates if a["name"].casefold() == expected.casefold()]
        if preferred:
            candidates = preferred
            break
    if len(candidates) != 1:
        raise BuildChoiceRequired(app, candidates, version, arch)
    return candidates[0]


def select_release(app: App, data: object, arch: str) -> Release:
    if not isinstance(data, dict) or data.get("draft"):
        raise ValueError("No published release is available.")
    prerelease = data.get("prerelease", False)
    is_latest = data.get("is_latest", not prerelease)
    if type(prerelease) is not bool or type(is_latest) is not bool:
        raise ValueError("GitHub returned invalid release flags.")
    if prerelease and not app.include_prereleases:
        raise ValueError(
            "Only prereleases are available. Choose 'Stable and prerelease versions' to use them."
        )
    tag = data.get("tag_name")
    version = release_version(tag)
    if version is None:
        raise ValueError(
            "This release's version tag is unsupported; expected e.g. v1.2.3 or studio-v0.6.0."
        )
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
    return Release(
        version, name, url, size, sha256.lower(), prerelease, is_latest and not prerelease
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


def newest_release(entries: list[Any], include_prereleases: bool) -> dict[str, Any]:
    published = [entry for entry in entries if isinstance(entry, dict) and not entry.get("draft")]
    eligible = [entry for entry in published if include_prereleases or not entry.get("prerelease")]
    if not eligible and any(entry.get("prerelease") for entry in published):
        raise ValueError(
            "Only prereleases are available. Choose 'Stable and prerelease versions' to use them."
        )
    recognised = [entry for entry in eligible if release_version(entry.get("tag_name")) is not None]
    if not recognised:
        if eligible:
            appimage = any(
                isinstance(asset, dict)
                and isinstance(asset.get("name"), str)
                and asset["name"].lower().endswith(".appimage")
                for entry in eligible
                if isinstance(entry.get("assets"), list)
                for asset in entry["assets"]
            )
            found = "An AppImage was found" if appimage else "Published releases were found"
            raise ValueError(f"{found}, but no release has a supported version tag.")
        raise ValueError("No published release with a supported version was found.")
    return max(recognised, key=lambda entry: version_key(release_version(entry["tag_name"])))


def api_entries(app: App, cancel: Event) -> list[Any]:
    check_cancel(cancel)
    prefix = f"https://api.github.com/repos/{app.repo}/releases"
    try:
        stable = json.loads(read_metadata(prefix + "/latest"))
    except MissingResource:
        stable = None
    if stable is not None and not isinstance(stable, dict):
        raise ValueError("GitHub returned invalid release metadata.")
    if not app.include_prereleases and stable is not None:
        return [{**stable, "is_latest": True}]
    check_cancel(cancel)
    items = json.loads(read_metadata(f"{prefix}?per_page={RELEASE_PAGES * 10}&page=1"))
    if not isinstance(items, list):
        raise ValueError("GitHub returned an invalid release listing.")
    entries: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            entries.append(
                {
                    **item,
                    "is_latest": bool(stable and item.get("tag_name") == stable.get("tag_name")),
                }
            )
    if stable is not None:
        entries.append({**stable, "is_latest": True})
    return entries


def api_release(app: App, cancel: Event) -> Release:
    data = newest_release(api_entries(app, cancel), app.include_prereleases)
    release = select_release(app, data, architecture())
    return with_checksum(app, str(data["tag_name"]), release, cancel)


class ReleaseListParser(HTMLParser):
    """Read release headers, excluding labels and links in release notes."""

    def __init__(self, app: App) -> None:
        super().__init__()
        self.tag_prefix = f"{app.source_url}/releases/tag/"
        self.entries: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.section_depth = 0
        self.header = False
        self.label = False
        self.owned_tag = False
        self.next_page = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("rel") == "next":
            self.next_page = True
        if tag == "section":
            anchor = attributes.get("data-release-anchor") or ""
            if self.current is not None:
                self.section_depth += 1
            elif anchor.startswith("release-"):
                self.current = {
                    "tag_name": anchor[8:],
                    "prerelease": False,
                    "is_latest": False,
                    "draft": False,
                }
                self.section_depth = 1
                self.header = True
                self.owned_tag = False
        if self.current is None or not self.header:
            return
        classes = (attributes.get("class") or "").split()
        if "markdown-body" in classes:
            self.header = False
        if tag == "a":
            url = urljoin("https://github.com", attributes.get("href") or "")
            if url.lower().startswith(self.tag_prefix.lower()):
                self.owned_tag |= unquote(url[len(self.tag_prefix) :]) == self.current["tag_name"]
        if tag == "span" and "Label" in classes:
            self.label = True

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.header and self.label:
            key = {"Pre-release": "prerelease", "Latest": "is_latest", "Draft": "draft"}.get(
                data.strip()
            )
            if key:
                self.current[key] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "span":
            self.label = False
        if tag == "section" and self.current is not None:
            self.section_depth -= 1
            if not self.section_depth:
                if self.owned_tag:
                    self.entries.append(self.current)
                self.current = None
                self.label = False


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
            # Compare decoded paths so a tag containing "/" matches whether GitHub links it
            # as "app/v1" or "app%2Fv1".
            decoded, prefix = unquote(url), unquote(self.download_prefix)
            if decoded.lower().startswith(prefix.lower()):
                name = decoded[len(prefix) :]
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


def page_entries(app: App, cancel: Event) -> list[Any]:
    """Public release URLs remain usable when the REST API quota is exhausted."""
    check_cancel(cancel)
    try:
        with request(f"https://github.com/{app.repo}/releases/latest") as response:
            location = str(response.geturl())
    except MissingResource:
        location = ""
    if location.rstrip("/").casefold() == f"{app.source_url}/releases".casefold():
        location = ""
    prefix = f"https://github.com/{app.repo}/releases/tag/"
    if location and not location.lower().startswith(prefix.lower()):
        raise ValueError("GitHub did not identify a stable release for this repository.")
    tag = unquote(location[len(prefix) :])
    if tag and (
        len(tag) > 128
        or any(c.isspace() or ord(c) < 32 for c in tag)
        or urlsplit(location).query
        or urlsplit(location).fragment
    ):
        raise ValueError("GitHub returned an invalid release tag.")
    data: dict[str, Any] = {"tag_name": tag, "prerelease": False, "is_latest": True}
    if app.include_prereleases or not tag:
        entries: list[Any] = []
        for page in range(1, RELEASE_PAGES + 1):
            check_cancel(cancel)
            parser_list = ReleaseListParser(app)
            url = f"{app.source_url}/releases" + (f"?page={page}" if page > 1 else "")
            parser_list.feed(read_metadata(url).decode("utf-8"))
            parser_list.close()
            if not parser_list.entries:
                raise ValueError("Could not read GitHub's public release listing; retry later.")
            entries.extend(parser_list.entries)
            if not parser_list.next_page:
                break
        if tag:
            entries.append(data)
        return entries
    return [data]


def page_release(app: App, cancel: Event) -> Release:
    data = newest_release(page_entries(app, cancel), app.include_prereleases)
    return page_release_asset(app, data, cancel)


def page_release_asset(app: App, data: dict[str, Any], cancel: Event) -> Release:
    tag = data["tag_name"]
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
    release = select_release(app, {**data, "assets": assets}, architecture())
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
    try:
        path = release_cache_path(app, cache_dir)
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
        release = release_from_cache(app, data["release"])
        github_status.release_result("Cached information", app.repo, checked_at)
        return release
    except (OSError, ValueError, KeyError, TypeError):
        return None


def release_from_cache(app: App, item: Any) -> Release:
    prefix = f"https://github.com/{app.repo}/releases/download/"
    url = item["url"]
    if not isinstance(url, str) or not url.lower().startswith(prefix.lower()):
        raise ValueError("Invalid cached download URL.")
    tag = unquote(url[len(prefix) :].split("/", 1)[0])
    release = select_release(
        app,
        {
            "tag_name": tag,
            "prerelease": item.get("prerelease", False),
            "is_latest": item.get("is_latest", True),
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
    if not release.sha256 or release.version != item["version"]:
        raise ValueError("Invalid cached release.")
    return release


def release_cache_path(app: App, cache_dir: Path) -> Path:
    suffix = "-prereleases" if app.include_prereleases else ""
    return cache_dir / f"{app.id}-{architecture()}{suffix}{build_cache_suffix(app)}.json"


def restore_rate_limits(cache_dir: Path) -> None:
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


def latest_release(app: App, cancel: Event, cache_dir: Path | None = None) -> Release:
    check_cancel(cancel)
    if cache_dir is not None:
        cached = cached_release(app, cache_dir)
        if cached is not None:
            return cached
        restore_rate_limits(cache_dir)
    try:
        release = api_release(app, cancel)
        github_status.release_result("GitHub API", app.repo, time.time())
    except RateLimited:
        if cache_dir is not None:
            with contextlib.suppress(OSError):
                atomic_json(cache_dir / "limits.json", _RETRY_AFTER)
        release = page_release(app, cancel)
        github_status.release_result("Public GitHub pages", app.repo, time.time())
    check_cancel(cancel)
    if cache_dir is not None:
        with contextlib.suppress(OSError):
            atomic_json(
                release_cache_path(app, cache_dir),
                {"checked_at": time.time(), "release": asdict(release)},
            )
    return release


def github_request_status() -> tuple[str, str, bool]:
    return github_status.describe(
        api_retry=_RETRY_AFTER.get("api.github.com", 0),
        public_retry=_RETRY_AFTER.get("github.com", 0),
    )


def download(release: Release, destination: Path, cancel: Event, progress: Progress) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", release.sha256):
        raise ValueError("A published SHA-256 checksum is required before downloading.")
    digest = hashlib.sha256()
    received = 0
    with request(release.url) as response, destination.open("wb") as output:
        while True:
            check_cancel(cancel)
            try:
                chunk = response.read(256 * 1024)
            except (OSError, HTTPException) as error:
                raise ValueError(
                    "The download was interrupted. The installed version was kept."
                ) from error
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
