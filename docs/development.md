# Development and packaging

## Layout

```text
src/appharbour/   Application code, icons and asset licences
tests/           Automated application tests
packaging/       AppImage scripts, entry point and dependency locks
.github/         GitHub Actions workflow
docs/            Usage, development and build documentation
docs/archive/    Historical research and earlier screenshots
```

Local generated directories are ignored by Git:

- `.venv/` — development environment.
- `build/` — packaging environment, cached downloads and temporary build files.
- `dist/` — finished AppImage and checksum file.
- `.backups/` — local recovery archives; preserve these when cleaning generated files.

## Set up

Use Python 3.11 or newer on a Linux desktop:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -O -m appharbour
```

## Checks

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m unittest discover -s tests -q
```

GUI tests use Qt's offscreen platform, disposable settings and controlled downloads.
They do not install creative applications into the user's library.

## Build the AppImage

Build on Linux x86_64 with Python 3.11 or newer, `binutils`, `tar` and `zstd` installed.

```sh
python3 -m venv build/packaging-env
build/packaging-env/bin/python -m pip install -r packaging/build-requirements.txt
build/packaging-env/bin/python packaging/build_appimage.py
```

Output goes to `dist/`: `AppHarbour-<version>-linux-x86_64.AppImage` and `SHA256SUMS.txt`.
The version comes from `pyproject.toml`.

Test the package without FUSE, using a disposable first-run home:

```sh
python3 packaging/smoke_test.py dist/AppHarbour-0.1.0-linux-x86_64.AppImage
```

Verify its checksum from inside `dist/` with `sha256sum -c SHA256SUMS.txt`.
Before a release, also exercise the tray, folder picker, themes and launching on real
X11/Wayland desktops. The headless check does not qualify those interactions.

## Bundled runtime

The build stages a portable [python-appimage](https://python-appimage.readthedocs.io/en/latest/apps/)
Python runtime, installs the application wheel and pinned runtime requirements, and bundles
selected Ubuntu 22.04 X11 libraries. User settings, installed apps and the development
environment are excluded.

`trim_appdir.py` removes unused Qt modules, QML components, development tools and pip.
It follows shared-library dependencies from retained Widgets bindings and desktop plugins.
Update its retained binding list if the application starts importing another Qt module.
Runtime licence texts and source/replacement instructions are bundled with the application;
keep [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) current when dependencies change.

Review these files together when updating the runtime:

| File | Purpose |
| --- | --- |
| `packaging/build-requirements.txt` | Build-tool version |
| `packaging/runtime-requirements.txt` | Bundled Python dependencies |
| `packaging/downloads.json` | Python AppImage, packaging tool and runtime hashes |
| `packaging/ubuntu-libs.json` | Bundled X11 library downloads and hashes |

Some upstream tool URLs use mutable `continuous` releases. A changed download fails the
hash check deliberately; review the replacement before updating its recorded hash.

## Local cleanup

Extracted AppImages, old comparison builds, logs and tool caches are disposable.
Keep `.backups/` separate from build output, and retain `dist/` while testing the current
AppImage. Recreating `build/packaging-env` and cached downloads requires network access.

See [GitHub Actions](builds.md) for the automated build process.
