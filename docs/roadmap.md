# Roadmap

Ideas for where AppGlider could go next. There are no promised dates or order, and plans
change after discussion. Something being listed here is not a promise that it will be built.

## Planned

- **Install queue:** line up several installs or updates, with clear progress and a way to
  cancel.
- **AppGlider updating itself:** check for new versions of AppGlider, show what changed and
  offer an update button. Today you update AppGlider by downloading the new AppImage.
- **Storage overview:** show how much space your apps use, including the old versions kept
  for roll back, and let you delete an old copy you no longer need.

## Under discussion

- **Arranging and hiding cards:** hide apps you do not use and put the rest in your own
  order. Still to decide: drag and drop or buttons, how a custom order works with search and
  the tabs, how to bring a hidden app back, and keyboard use. Hiding an app would never
  remove it or its installed files.
- **Desktop notification for updates:** a pop-up message when a new update appears. The
  tray badge and tooltip already show waiting updates; a notification would be optional.
- **Offline starter catalog:** a copy of the example catalog built into AppGlider, with an
  **Import starter catalog** button, so a first run does not need the file downloaded. Today
  the example is in the repository ([`catalogs/artcraft.toml`](catalogs/artcraft.toml)).

## Done

- **Scheduled update checks and a tray badge:** choose how often AppGlider checks (every
  3, 6, 12 or 24 hours, or only when you ask), and see an amber badge on the tray icon
  while updates are waiting.
- **A fresh start for new users:** the library is empty at first, and the ArtCraft apps and
  SolidCraft are an example catalog you can import instead of being built in.
- **Catalogs:** import and export plain text lists of apps, with a preview before anything
  is added.
- **GitHub status:** a footer label and a settings page show how many of GitHub's
  anonymous requests are left and when checks were last made.
