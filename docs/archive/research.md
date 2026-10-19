# ArtCraft launcher research

Research date: 19 October 2026. Scope: a Nim launcher for Linux AppImages of the 13 ArtCraft suite apps, SolidCraft, and selected additional GitHub projects.

This is a repository and source inspection, not runtime qualification or a security audit. Features described only in a README are distinguished from inspected implementation details below. Repository contents can change.

## Findings

| Project | Stack / licence | Relevance |
| --- | --- | --- |
| [Official ArtCraft Launcher](https://github.com/storytold/craft-launcher) | Rust, egui; MIT OR Apache-2.0 | Reference for the current 13-app catalog, release parsing, checksum validation, desktop integration, and AppImage launch environment. Broad cross-platform scope; its contribution policy reserves third-party app inclusion for maintainers. |
| [ArtCraft Toolbox](https://github.com/smyrgeorge/artcraft-toolbox) | Rust, egui; MIT OR Apache-2.0 | Strongest initial reference for our installation and catalog model. TOML catalog, configurable repository and bundle IDs, AppImages kept in separate version directories, rollback and CLI documented. |
| [CraftLauncher — Eric Vogt](https://github.com/EricVogt93/artcraft-launcher) | Rust, egui; Apache-2.0 | Useful reference for rollback, running-app update deferral, desktop integration and launcher recovery. Extracts AppImages once into its managed library rather than retaining only the single-file execution model. |
| [Craft Apps Manager](https://github.com/CryptoKey98/craft-apps-manager) | Rust; MIT | Custom GitHub app support, package identity recognition, per-app settings and multiple installations are documented. Directly relevant to adding repositories outside Storytold, but its source builder, backups and native installer support exceed our scope. |
| [LandingCraft](https://github.com/rallegretti/LandingCraft) | Rust, egui; CC0-1.0 | Relatively focused desktop alternative. Supports AppImages, existing-install detection and configurable install location. Defaults to tarballs, so it would need narrowing for our use. |
| [ArtCraft Hub](https://github.com/tinyplayerss/artcraft-hub-launcher) | Python, Tkinter; MIT | Accessible reference for a simple editable products list containing repository metadata. Supports Linux downloads. Verification is optional in the inspected download path. |
| [Linux ArtCraft Launcher — omi-the-sorcerer](https://github.com/omi-the-sorcerer/artcraft-launcher) | Rust, egui; licence undecided | Closest documented scope: Linux AppImages only, keyboard search, desktop integration and GitHub caching. Cannot assume permission to copy or translate its code while the licence is undecided. |
| [ArtCraft Master Suite](https://github.com/ZifuM/Craft-apps-launcher-installer) | Rust; MIT source licence | Supports Linux and supplies an AppImage launcher build. Has expanded into assets, plugins, projects and backups. README explicitly warns that older self-updaters require a manual upgrade. Useful context, but substantially wider than our project. |
| [ArtCraft Suite Manager](https://github.com/erikerikerik/artcraft-suite) | Production C#/Avalonia; Apache-2.0; older Rust branch also maintained | Windows-first, so not an AppImage implementation candidate. Its documented download resumption, transactional updates and recovery journal are useful behavioural references. |
| [CraftHub](https://github.com/MegaTheLEGEND/CraftHub) | Rust, axum; MIT licence file inspected | Already documents the 13 suite apps plus SolidCraft and custom repositories. It installs and serves web builds through Docker, with desktop download links; it is not a local AppImage manager. |

No Nim implementation was found in this search. That is not proof one does not exist.

## Source observations

### ArtCraft Toolbox

- `crates/catalog/catalog.toml` holds the built-in catalog.
- `crates/catalog/src/lib.rs` accepts valid `owner/repository` values, supports explicit bundle IDs, former asset slugs and custom asset patterns. Its catalog validator is not restricted to Storytold repositories. This establishes a useful data model, not proof that arbitrary apps install successfully end to end.
- `crates/install/src/appimage.rs` validates ELF and type-2 AppImage magic, stages a file, sets executable permissions and renames its staging directory into a version directory.
- Installation layout is `<apps>/<id>/<version>/<id>.AppImage`.
- Desktop entries carry an ownership marker; an existing entry without that marker is left alone.
- Desktop `Exec` quoting includes spaces, shell-related characters and percent field codes, with focused tests.
- Removal checks the expected path layout and refuses a running version.

These are strong candidates for selective behavioural porting. Signed feeds, remote catalog delivery, MCP support and cross-platform installers can remain outside the first Nim version.

Source: [AppImage implementation](https://github.com/smyrgeorge/artcraft-toolbox/blob/main/crates/install/src/appimage.rs), [catalog implementation](https://github.com/smyrgeorge/artcraft-toolbox/blob/main/crates/catalog/src/lib.rs).

### Closest AppImage-only launcher

The README of omi-the-sorcerer's project describes checksum verification, but `src/store.rs` permits installation when the checksum file is missing, fetching it fails, or the asset has no matching entry. A checksum mismatch does reject the download. After activating the new file, it removes older files from the app's directory. Therefore it is useful as a UX reference, but does not meet a strict verification-and-rollback design unchanged.

The README's licence section says the licence is pending. Treat it as a design reference rather than a source donor until permission is established.

Source: [installation code](https://github.com/omi-the-sorcerer/artcraft-launcher/blob/main/src/store.rs), [README](https://github.com/omi-the-sorcerer/artcraft-launcher).

### Python ArtCraft Hub

`artcraft-hub/hub/core.py`, function `fetch`, verifies only if a recognised checksum asset exists. Its checksum check searches for the downloaded file's hash anywhere in the checksum text, rather than binding a parsed checksum row to the selected filename. This should not be copied as our verification contract.

Source: [download implementation](https://github.com/tinyplayerss/artcraft-hub-launcher/blob/main/artcraft-hub/hub/core.py).

### FUSE and extraction

Eric Vogt's README and `crates/core/src/platform.rs` describe and implement extraction of AppImages into the managed library. LandingCraft documents direct AppImage execution with FUSE 2 and extraction fallback when unavailable. Our use of AppImages as the download format does not settle whether execution should mount them directly or use extraction. Test that choice on the target desktop before fixing the launch design.

## Recommendation for the Nim project

Use ArtCraft Toolbox as the primary reference for a data-driven catalog and versioned AppImage storage. Use the official launcher as a second reference for suite-specific release conventions and launch behaviour. Consult Craft Apps Manager for non-Storytold repository identity, and Eric Vogt's project for recovery behaviour.

Port the required behaviour and meaningful fixtures into Nim; rebuild the GUI. Preserve source attribution for translated code. Start with a fixed local catalog for the 14 requested apps, strict published SHA-256 verification, architecture selection, staged installation, launch, explicit update, previous-version rollback and ownership-marked desktop entries. Cache release metadata so an offline launch still works.

Do not copy project discovery, cloud backup, source compilation, plugins, remote catalog signing infrastructure or cross-platform package support into this first release.

The official source is permissively licensed, but its launcher name and marks have separate restrictions. A distributed derivative needs independent launcher branding. Individual retained assets require their own attribution review: [official MIT licence](https://github.com/storytold/craft-launcher/blob/main/LICENSE-MIT), [brand terms](https://github.com/storytold/craft-launcher/blob/main/docs/brand/LICENSE-brand.txt).

## Limits

Read documentation for the ten projects listed above and inspected selected source from the official launcher and six community projects. No launchers or installers were executed, no test suites were run, and no claim of production reliability is made. Release histories, all dependency licences, and compatibility of all 14 current app releases were not exhaustively audited. Source clones and fetched READMEs are under `/tmp/artcraft-launcher-research`; the official launcher clone is `/tmp/artcraft-launcher-assessment`.
