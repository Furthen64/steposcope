# Reliable shortcut, remembered confirmation, and paused positions

Ctrl+P replaces Space as the play/pause shortcut. It is an application-wide QAction with autorepeat disabled, so focused buttons/text fields cannot redirect it into their normal Space behavior. Locked viewport filters allow this shortcut through while continuing to consume other viewport input. The viewport notice, hotkey legend, and play-button tooltip now show Ctrl+P.

Continue anyway now acknowledges the duration warning for the currently loaded document. Pause/resume does not prompt again. Successfully opening another document (or reloading the same one) resets this in-memory acknowledgement. The existing Do not ask again for this file checkbox still provides persistent suppression across openings. Cancelling the dialog does not acknowledge it.

Show positions now controls playback visibility. Paused previews always display position markers for inspection, while retaining the saved playback setting. Pausing explicitly refreshes the current snapshot, restores navigation, and reveals the markers. Resuming hides them again if Show positions is off. Its tooltip describes this distinction. This can make pausing a large point-heavy scene expensive, since marker actors must be rebuilt; point batching remains a future optimization.

Validation: 41 tests passed across playback locking, playback listing, duration confirmation/reporting, and camera behavior. Added an actual Qt shortcut test with button and text-field focus, and a test showing acceptance without the permanent checkbox is remembered until the next document open. Existing visibility tests now verify paused markers are restored without changing the saved playback preference.

This supersedes the Space shortcut and paused visibility behavior described in earlier experiment notes. It does not change Turbo batch sizes, presentation cadence, or grid/axis sizing.
