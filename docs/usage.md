# Using AppHarbour

## Library

Use **All apps** to browse the catalog, **Installed (N)** for managed installations and
**Updates (N)** for known updates. Counts show full library totals independently of search.
Search matches application names, descriptions and repository names.

**Install** downloads the selected release for your CPU, checks its published SHA-256,
validates the AppImage header and creates an application-menu shortcut. Installed cards
offer **Launch**. Loose AppImages and installations from other launchers are ignored.

## Updates and removal

**Refresh releases** checks the whole catalog, including apps you have not installed.
On startup, cached release information appears first, followed by background checks.
Successful results less than five minutes old are reused; older results are refreshed
from GitHub. Clicking **Refresh releases** bypasses this freshness window. These checks
do not download or install applications. Use **Update** on a card or **Update all** to install eligible versions.
One previous version is normally retained for **⋯ → Roll back**.
Failed or cancelled downloads leave the active version intact.

**⋯ → Uninstall…** removes AppHarbour-owned AppImages and their shortcut. Documents and
the application's own settings are preserved. An application cannot be uninstalled while
its managed version is running.

Checks run on startup or when requested; there are no scheduled periodic checks.
AppHarbour itself is upgraded by replacing its AppImage and
launching the replacement; it does not currently update itself.

## Stable and prerelease versions

Stable releases are the default. Use **⋯ → Release preferences…** on a card, or
**Settings → Applications → Repositories…**, to include previews for that application. It applies to
built-in apps and added repositories, survives restarts and is used by future updates.
**Check available release** previews the choice without installing anything.

**Stable + previews** (called **Stable and prerelease versions** in repository management)
chooses the highest supported version among recent published stable releases and previews. For example, `1.3.0-beta.10` follows
`1.3.0-beta.2`, and `1.3.0` follows both. A final stable release therefore replaces a beta
when its version is newer. Draft releases are excluded.

Turning previews off keeps an installed preview launchable and waits for a newer stable
release. It never automatically downgrades to an older stable version. The explicit
**Roll back** action can still restore the previous installed version.

Green **Latest** badges identify GitHub's designated latest stable release. Amber
**Pre-release** badges identify preview releases. Each badge belongs to the adjacent
version: an installed preview and an available stable update can show different badges.

Discovery examines up to 30 recent releases, plus GitHub's designated latest stable
release. The API normally uses two metadata requests per repository: the latest stable release
and a recent-release listing. Checksums can require additional requests. The public
fallback examines up to three pages of ten releases. Unrecognised version tags are excluded.
If the newest eligible version lacks a compatible AppImage or checksum, discovery explains the problem rather
than silently choosing an older download. Previews use the same CPU and SHA-256 checks
as stable releases. Cards show a newer preview even when you follow stable releases;
that notice does not count as an update until previews are enabled. Changing the release preference
saves your choice without installing anything. GitHub's public-page fallback also respects
this preference when its API quota is exhausted. If a project only publishes previews, AppHarbour explains
that previews must be enabled before adding it.

Checked release information is retained across restarts, even after its five-minute
request cache expires. Cards show when it was checked, whether a refresh is needed and
whether a refresh failed. Failed checks keep previously known versions and badges visible.
Downloads still require a current successful lookup and verification. Background checks
run one app at a time and keep application launching and release preferences available.
The separate **Refresh library** button has been removed; local records reload alongside
release refreshes and application changes.

## Additional repositories

Choose **+ Add app**, enter a public GitHub repository URL and inspect its release.
Keep **Stable releases only** selected, or choose **Stable and prerelease versions**.
The release and icon options appear after inspection.
Adding the repository creates a card; it does not install the app.
Repository, icon and release preference changes are saved immediately, independently of
Settings' Save/Cancel buttons.
Uninstall a custom app before removing its repository from the catalog.

Inspection also looks for application icons in the repository. Preview the discovered
candidates and choose one from the icon list, or use **Choose icon…** to select a local
PNG or self-contained SVG. Adding the repository saves the selected image locally.

To change an existing custom entry's icon, select it in the repository list and use
**Find icons** or **Choose icon…**. These icon changes save immediately. The selected icon
appears on its card, details, tray entry and managed-app menu shortcut, and stays available
offline. Built-in application icons remain bundled with AppHarbour.

Icons are limited to 2 MiB and 4096 pixels per side, then converted to a PNG of at most
512 pixels per side. SVGs with external resources or document types are rejected.
Discovery examines a bounded set of likely repository files, so some projects need a
manual choice. An unavailable icon or GitHub quota error does not prevent adding a valid
application; AppHarbour's icon is the fallback. Embedded AppImage icons are not extracted.

Supported release tags include `v1.2.3`, `1.2.3` and `v1.2.3-beta.1`. AppHarbour inspects
the release's actual assets for filenames ending in `.AppImage`, ignoring letter case. The repository
name, version placement, separators and word `linux` are not required. Companion files
such as `.AppImage.zsync` and `.AppImage.asc` are excluded.

Architecture labels are matched to the host: `x86_64`, `x86-64`, `x64` and `amd64`
identify x86_64; `aarch64` and `arm64` identify ARM64. An explicit match takes priority
over an unlabelled AppImage. A single unlabelled candidate is allowed; installation
checks the actual file's CPU architecture and AppImage header before activating it.
The AppHarbour launcher package currently targets Linux x86_64 only.

When several candidates fit, familiar repository-based filenames retain their existing
preference. If that does not identify one file, inspection or update checking reports
the ambiguous filenames and stops. There is no manual asset picker yet.

A published SHA-256 checksum is required. GitHub's asset digest is used when available;
otherwise the release must provide a matching entry in `SHA256SUMS.txt`. When GitHub's
API quota is exhausted, the public-download fallback reads the actual asset links and
filename-labelled digests from GitHub's public release listing, using the same selection
rules. A separate checksum file is unnecessary when GitHub publishes the digest.
If GitHub changes the page format or no checksum can be obtained, discovery stops with
an explanation. No GitHub token is needed.

## Settings and desktop integration

Settings has General, Applications, Storage and About sections.
The version button at the top right opens About.
Choose System / Light / Dark appearance and the folder for new installations.
Changing the folder leaves existing installations in place; their updates stay with them.

AppHarbour creates its own menu entry. Managed apps get entries such as
**PhotoCraft (AppHarbour)**. Updates reuse the existing entry, and
**⋯ → Create/repair menu shortcut** restores a missing managed-app shortcut.

The system tray offers **Open AppHarbour**, **Refresh releases**, installed apps and **Quit**.
Closing the window hides it when a tray is available; downloads continue in the background.
Turn this off in **Settings → General** if closing should exit.
Without a working tray, closing exits normally. Use **Quit AppHarbour** to exit from the tray.

Installed applications remain launchable offline. Failed app startup reports a diagnostic
log path in the launcher. Downloading and checking releases requires network access.
