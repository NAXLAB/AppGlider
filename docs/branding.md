# AppHarbour

AppHarbour is the user-facing name. Its icon depicts three application tiles nested in a
blue harbour, with teal, blue and coral colours and a transparent background.

The production source asset is `src/appharbour/assets/icons/appharbour.png`.
It was generated on 2026-10-19 using the built-in imagegen tool. Qt renders the same PNG at
the sizes required by the window, header, desktop shortcut and tray; no extra dependency is
needed. The application catalog's upstream icons remain separate.

## Generation prompt

> Use case: logo-brand. Asset type: production Linux desktop application icon for AppHarbour, an installer/updater/launcher that gives a collection of AppImages a safe home. Create ONE polished, distinctive square app icon, centered isolated on a genuinely transparent background. Subject: three compact rounded application tiles with subtly different heights, nested in a simple sweeping U-shaped harbour/dock, the combined silhouette hinting at a welcoming harbour and an upward launch. Use a deep indigo/navy dock with a strong cyan/teal rim and three tile faces in teal, blue, and warm coral. Restrained soft dimensional shading, crisp smooth contours, bold simple geometry, friendly modern desktop identity. The tiles must read as application tiles, not buildings, books, or shopping bags. Composition: one unified compact mark occupying about 85% of a square canvas, generous transparent safety margin, balanced front view with only slight depth, robust recognisability at 24px. No letters, no text, no wordmark, no badges, no border, no surrounding scene, no circular background plate, no mockup, no tiny details, no drop shadow beyond the mark. Deliver a single 1024x1024 icon with transparent alpha.

## Application identity

- Display name and Qt organisation/application IDs: `AppHarbour`.
- Python module and command: `appharbour`.
- Library, custom repositories, release cache and icons: `$XDG_DATA_HOME/appharbour/`
  (normally `~/.local/share/appharbour/`).
- Preferences: `~/.config/AppHarbour/AppHarbour.conf`, respecting `XDG_CONFIG_HOME`.
- Installation ownership marker: `.appharbour`, with `AppHarbour` as its first line.
- Managed-app shortcuts: `appharbour-<id>.desktop`, marked `X-AppHarbour=true`.
- Launcher shortcut and Qt desktop-file ID: `appharbour.desktop` / `appharbour`.

## Tray behaviour

Click restores the window. The context menu opens AppHarbour, checks updates, launches
managed installed apps or quits. Closing hides only while a tray is available; otherwise
normal close behaviour remains. Downloads continue while hidden, and Quit waits for a
confirmed cancellation to finish before exiting. Autostart and notifications are not added.

Qt tray integration follows the [QSystemTrayIcon documentation](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html).
