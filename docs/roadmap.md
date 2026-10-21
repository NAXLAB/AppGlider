# Roadmap

Planned improvements for AppGlider have no promised release dates or fixed implementation
order. Designs may change after discussion.

## Planned

- **Installation queue:** queue multiple installs or updates with clear progress and
  cancellation controls.
- **AppGlider updates:** check for launcher updates, show release notes and offer an
  explicit update action.
- **Storage overview:** show space used by managed applications and their retained
  rollback versions, with controls for removing an unwanted rollback copy.

## Completed

- **Empty first run:** a new library is empty and offers **+ Add app** and **Import catalog…**.
  The ArtCraft apps and SolidCraft are an example catalog in `docs/catalogs/`, not a built-in
  list; every app can be removed. Those apps keep their bundled icons and established ids,
  so installs made before importing are recognised again once the catalog is imported.
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

## Offline starter catalog — proposal

Ship a copy of the example catalog inside the AppImage with an **Import starter catalog**
action, so the first run works without downloading the file. Not started; the example is
currently published in the repository (`docs/catalogs/artcraft.toml`) and can be attached to
each release.
