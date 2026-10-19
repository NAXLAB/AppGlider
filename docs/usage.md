# Using AppHarbour

## Library

Use **All apps** to browse the catalog, **Installed (N)** for managed installations and
**Updates (N)** for known updates. Counts show full library totals independently of search.
Search matches application names, descriptions and repository names.

**Install** downloads the latest stable release for your CPU, checks its published SHA-256,
validates the AppImage header and creates an application-menu shortcut. Installed cards
offer **Launch**. Loose AppImages and installations from other launchers are ignored.

## Updates and removal

**Check updates** checks installed apps. Use **Update** on a card or **Update all** to install
available versions. One previous version is normally retained for **⋯ → Roll back**.
Failed or cancelled downloads leave the active version intact.

**⋯ → Uninstall…** removes AppHarbour-owned AppImages and their shortcut. Documents and
the application's own settings are preserved. An application cannot be uninstalled while
its managed version is running.

Update checks are manual. AppHarbour itself is upgraded by replacing its AppImage and
launching the replacement; it does not currently update itself.

## Additional repositories

Open **Settings → Repositories… → Add repository**, enter a public GitHub repository URL
and inspect its latest stable release. The release and icon options appear after inspection.
Adding the repository creates a card; it does not install the app.
Repository changes are saved immediately, independently of Settings' Save/Cancel buttons.
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

Supported release tags are `v1.2.3` or `1.2.3`. Supported filenames are:

```text
<repository>-<version>-linux-<architecture>.AppImage
<repository>-linux-<architecture>.AppImage
<repository>-<version>-<architecture>.AppImage
<repository>-<architecture>.AppImage
```

Filename matching ignores letter case, while downloads use the exact published filename.

Architectures are `x86_64` (also `x64` or `amd64`) and `aarch64` (also `arm64`),
matched to the host. This describes managed application downloads; the AppHarbour
launcher package currently targets x86_64 only.
Other release naming conventions are not supported yet.

A published SHA-256 checksum is required. GitHub's asset digest is used when available;
otherwise the release must provide `SHA256SUMS.txt`. When GitHub's API quota is exhausted,
the public-download fallback also requires that file. No GitHub token is needed.

## Settings and desktop integration

Settings groups appearance, downloads and repository management into separate sections.
Choose System / Light / Dark appearance and the folder for new installations.
Changing the folder leaves existing installations in place; their updates stay with them.

AppHarbour creates its own menu entry. Managed apps get entries such as
**PhotoCraft (AppHarbour)**. Updates reuse the existing entry, and
**⋯ → Create/repair menu shortcut** restores a missing managed-app shortcut.

The system tray offers **Open AppHarbour**, **Check updates**, installed apps and **Quit**.
Closing the window hides it when a tray is available; downloads continue in the background.
Without a working tray, closing exits normally. Use **Quit AppHarbour** to exit from the tray.

Installed applications remain launchable offline. Failed app startup reports a diagnostic
log path in the launcher. Downloading and checking releases requires network access.
