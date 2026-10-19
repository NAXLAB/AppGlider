"""Built-in applications and validated, locally saved GitHub repositories."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from appharbour import APP_ICON

ASSETS = Path(__file__).parent / "assets"


@dataclass(frozen=True)
class App:
    id: str
    name: str
    description: str
    repo: str
    asset_stem: str = ""

    @property
    def icon(self) -> Path:
        icon = ASSETS / "icons" / f"{self.id}.png"
        return icon if icon.is_file() else APP_ICON

    @property
    def source_url(self) -> str:
        return f"https://github.com/{self.repo}"


APPS = (
    App("photocraft", "PhotoCraft", "Image editing and photo composition", "storytold/photocraft"),
    App("vectorcraft", "VectorCraft", "Vector illustration and graphics", "storytold/vectorcraft"),
    App("filmcraft", "FilmCraft", "Video editing, colour and sound", "storytold/filmcraft"),
    App(
        "lightcraft", "LightCraft", "Photo organisation and RAW development", "storytold/lightcraft"
    ),
    App("pdfcraft", "PdfCraft", "Read, organise and work with PDFs", "storytold/pdfcraft"),
    App(
        "effectcraft", "EffectCraft", "Motion graphics and visual effects", "storytold/effectcraft"
    ),
    App("designcraft", "DesignCraft", "Page layout and publishing", "storytold/designcraft"),
    App("figcraft", "FigCraft", "Interface design and prototyping", "storytold/figcraft"),
    App("wordcraft", "WordCraft", "Writing and document design", "storytold/wordcraft"),
    App("soundcraft", "SoundCraft", "Audio recording, editing and mixing", "storytold/soundcraft"),
    App("deckcraft", "DeckCraft", "Presentations and slide decks", "storytold/deckcraft"),
    App("cadcraft", "CADCraft", "Computer-aided design and drafting", "storytold/cadcraft"),
    App("gridcraft", "GridCraft", "Spreadsheets and data", "storytold/gridcraft"),
    App("solidcraft", "SolidCraft", "Parametric 3D modelling and CAD", "tangency-labs/solidcraft"),
)


def custom_app(repository: str, name: str | None = None) -> App:
    value = repository.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("The repository address contains control characters.")
    if "://" in value:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Use a public GitHub repository URL, such as https://github.com/owner/app."
            )
        value = parsed.path.strip("/")
    value = value.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", value):
        raise ValueError("Enter a GitHub repository URL or owner/repository, without extra paths.")
    owner, slug = value.lower().split("/")
    if slug in (".", ".."):
        raise ValueError("The repository name is invalid.")
    title = name.strip() if name is not None else slug.replace("-", " ").replace("_", " ").title()
    if not title or len(title) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in title):
        raise ValueError("Use an application name of 1–128 characters, without control characters.")
    # shortcut: match repository-based AppImage names until other release layouts are needed.
    return App(
        f"github-{owner}__{slug}", title, f"AppImage from {owner}/{slug}", f"{owner}/{slug}", slug
    )


def load_apps(path: Path) -> tuple[App, ...]:
    if not path.exists():
        return APPS
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("The repository list is too large.")
        data = json.loads(path.read_text())
        if not isinstance(data, list) or len(data) > 100:
            raise ValueError("Expected a list of up to 100 custom repositories.")
        apps = list(APPS)
        for item in data:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("repo"), str)
                or not isinstance(item.get("name"), str)
            ):
                raise ValueError("Invalid repository entry.")
            app = custom_app(item["repo"], item["name"])
            if any(
                known.repo.casefold() == app.repo.casefold() or known.id == app.id for known in apps
            ):
                raise ValueError(f"Duplicate repository: {app.repo}.")
            apps.append(app)
        return tuple(apps)
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {path}. The file was preserved: {error}") from error
