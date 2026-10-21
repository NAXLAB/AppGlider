# AppGlider

AppGlider is the user-facing name. Its icon is a swept wing of three
S-curved feathers in three greens (`#8FAE75`, `#5B7F52`, `#3F5E3A`) on a transparent
background, so it reads on both light and dark desktops and panels. The Forest theme's
palette is `#1C1F1A`, `#5B7F52`, `#8FAE75`, `#D6D1C4` and `#F4F3EB`. The name replaced
AppHarbour, which was already in use.

Settings > Appearance offers System (the desktop's native colours, the default), Light,
Dark, Forest, Earthy and Sandstone.

The vector source is `docs/branding/appglider-icon.svg`, drawn in VectorCraft: the top
feather's curve is copied, rotated and scaled for the other two, with real 12 pt gaps cut
between them because Qt's SVG renderer ignores masks.
`src/appglider/assets/icons/appglider.png` is its 1024 px render. Qt renders the same PNG at
the sizes required by the window, header, desktop shortcut and tray; no extra dependency is
needed. The bundled icons for the ArtCraft example catalog's apps remain separate.

## Application identity

- Display name and Qt organisation/application IDs: `AppGlider`.
- Python module and command: `appglider`.
- Library, custom repositories, release cache and icons: `$XDG_DATA_HOME/appglider/`
  (normally `~/.local/share/appglider/`). The release cache holds `etags.json`; the data
  folder also holds `library.lock` and `appglider.lock` (single-instance guard, whose
  wake-up socket lives in `$XDG_RUNTIME_DIR`).
- Preferences: `~/.config/AppGlider/AppGlider.conf`, respecting `XDG_CONFIG_HOME`.
- Installation ownership marker: `.appglider`, with `AppGlider` as its first line.
- Managed-app shortcuts: `appglider-<id>.desktop`, marked `X-AppGlider=true`.
- Launcher shortcut and Qt desktop-file ID: `appglider.desktop` / `appglider`.

## Tray behaviour

Click restores the window. The context menu opens AppGlider, checks updates, launches
managed installed apps or quits. Closing hides only while a tray is available; otherwise
normal close behaviour remains. Downloads continue while hidden, and Quit waits for a
confirmed cancellation to finish before exiting. Autostart and notifications are not added.

Qt tray integration follows the [QSystemTrayIcon documentation](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html).
