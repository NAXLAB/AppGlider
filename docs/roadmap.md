# Roadmap

Planned improvements for AppGlider have no promised release dates or fixed implementation
order. Designs may change after discussion.

## Planned

- **Empty first run:** start with an empty library and an **Import catalog…** button, with
  the ArtCraft apps and SolidCraft offered through the example catalog instead of being
  built in. See [Catalogs and an empty first run](#catalogs-and-an-empty-first-run--design-discussion).
- **Installation queue:** queue multiple installs or updates with clear progress and
  cancellation controls.
- **AppGlider updates:** check for launcher updates, show release notes and offer an
  explicit update action.
- **Storage overview:** show space used by managed applications and their retained
  rollback versions, with controls for removing an unwanted rollback copy.

## Completed

- **Import/export catalogs:** import and export plain-text TOML catalogs from the
  Repositories dialog, with a preview before anything is added. An example for the ArtCraft
  apps and SolidCraft is in `docs/catalogs/`. See the [usage guide](usage.md#import-and-export-catalogs).

- **GitHub request status:** a footer indicator and details in Settings show the last
  reported API allowance, retry times, the most recent release-information source and
  when that information was checked. Opening the status panel makes no requests.

## Library customisation — design discussion

Explore hiding unwanted applications and letting users arrange cards in their preferred
order. The interaction needs further discussion before implementation, including:

- Whether cards should move by drag-and-drop, an explicit arrangement mode, or move controls.
- How a saved custom order interacts with searching and the All apps, Installed and Updates views.
- How hidden applications are restored and how installed apps with updates remain discoverable.
- Keyboard access and preserving the layout across restarts and different window sizes.

Hiding a card should preserve its repository definition and any installed AppImage.

## Tray update indicator

Proposed: show a distinct tray icon state when updates are pending, such as a small accent
dot on the wing. Do not rely on colour alone, and check how common desktop themes recolour
tray icons. The tooltip already reports the update count.

## Catalogs and an empty first run — design discussion

Catalog import and export are built (see Completed). What remains is proposed: AppGlider
starts with an empty library and no built-in vendor list. Today the
13 ArtCraft applications and SolidCraft are built in, which makes AppGlider look like a
launcher for one product family. The first window would offer **+ Add app** and
**Import catalog…**, and the ArtCraft apps would become an example catalog file instead.

Remaining design points:

- Imported apps show the generic icon until **Find icons** runs, and icon discovery needs
  at least two GitHub requests per app, so it must not run for many apps at once
  (unauthenticated GitHub allows 60 requests an hour). Proposed: keep a small bundled set
  of icons, looked up by repository, as a fallback for the example catalog's apps.
- Custom repositories were checked against the built-in apps for the latest release of
  PhotoCraft, PdfCraft and SolidCraft (same release and asset). Older releases and the
  other apps are unchecked.
- Removing the built-in list means every app becomes removable, the built-in/custom split
  in the library and dialogs goes away, and the tests that use the built-in list need a
  fixture catalog.
- Attach the example catalog to each release, and optionally ship a copy for an offline
  **Import starter catalog** action.
