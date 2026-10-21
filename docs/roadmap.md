# Roadmap

Planned improvements for AppGlider have no promised release dates or fixed implementation
order. Designs may change after discussion.

## Planned

- **Import/export catalogs:** back up, share or publish a list of repositories as a
  plain-text TOML file that anyone can read and write by hand. See
  [Catalogs and an empty first run](#catalogs-and-an-empty-first-run--design-discussion).
- **Installation queue:** queue multiple installs or updates with clear progress and
  cancellation controls.
- **AppGlider updates:** check for launcher updates, show release notes and offer an
  explicit update action.
- **Storage overview:** show space used by managed applications and their retained
  rollback versions, with controls for removing an unwanted rollback copy.

## Completed

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

Proposed: AppGlider starts with an empty library and no built-in vendor list. Today the
13 ArtCraft applications and SolidCraft are built in, which makes AppGlider look like a
launcher for one product family. The first window would offer **+ Add app** and
**Import catalog…**, and the ArtCraft apps would become an example catalog file instead.

Proposed catalog format: a TOML file, read with the standard library's `tomllib`, so a
reader can see exactly what an import adds and a text editor is enough to write one.
Sketch (repository URL is a placeholder):

```toml
format = 1
name = "My starter catalog"

[[app]]
name = "Example App"
repo = "https://github.com/owner/project"
stable_only = true
```

Design points to settle before implementation:

- Import shows a preview before merging ("adds 14 apps, 2 already exist"). It adds
  definitions only; nothing installs automatically.
- Apply the existing repository checks to every entry (GitHub only, the redirect and
  filename rules) and bound file size and app count. Reject an unknown `format`; ignore
  and report unknown keys. Start without importing from a URL.
- Catalogs hold definitions, not icons. Confirm that discovered repository icons, stored
  offline, are enough for the ArtCraft apps, which have bundled icons today.
- Confirm that a custom repository gets everything a built-in app gets: release
  preferences, build choices and icons. Tests that use the built-in list would need a
  fixture catalog.
- Writing a catalog needs only a small writer for strings, booleans and a list of tables;
  check it by round-tripping through `tomllib`. No new dependency.
- Publish the ArtCraft and SolidCraft example in the repository and attach it to each
  release; optionally ship a copy for an offline **Import starter catalog** action.
- Treat this as a feature after the 0.4.0 rename release.
