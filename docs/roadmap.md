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

- **Moving cards by hand and hiding apps:** sorting and pinning are done. Dragging cards into
  your own order, or hiding an app without removing it, would need more work and only makes
  sense if people ask for it. Removing an app from the list and adding it again with a
  catalog already covers most of "hide".
- **Desktop notification for updates:** a pop-up message when a new update appears. The
  tray badge and tooltip already show waiting updates; a notification would be optional.

## Done

- **Sorting and pinning:** a **Sort** box (name, installed first, updates first, order added)
  and **Pin to top** on each card, remembered between runs.
- **Example catalog built in:** an **Example catalog** button on the welcome screen and in the
  Repositories window adds the ArtCraft apps and SolidCraft without downloading anything.
- **Cheaper update checks:** checking an app that has not changed now asks GitHub's ordinary
  release page first (or its public release feed, for apps with previews turned on) and uses
  none of the free request allowance. The update schedule can now run as often as every hour.
- **Scheduled update checks and a tray badge:** choose how often AppGlider checks (every
  3, 6, 12 or 24 hours, or only when you ask), and see an amber badge on the tray icon
  while updates are waiting.
- **A fresh start for new users:** the library is empty at first, and the ArtCraft apps and
  SolidCraft are an example catalog you can import instead of being built in.
- **Catalogs:** import and export plain text lists of apps, with a preview before anything
  is added.
- **GitHub status:** a footer label and a settings page show how many of GitHub's
  anonymous requests are left and when checks were last made.
