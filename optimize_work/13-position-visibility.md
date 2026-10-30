# Position markers are independent of Turbo

Added a checkable **Show positions** control to the top toolbar and View menu. It defaults to enabled and persists as `show_positions` in QSettings.

Turbo no longer hides Cartesian point or vertex markers when the first line, control net, or face appears. It retains batching and the existing triangle-edge suppression. Its tooltip now describes the separate position control.

Show positions applies while paused, during playback, and with Turbo on or off. Toggling it redraws the current snapshot immediately. Face-analysis boundary vertices retain their existing visibility rules so the analysis remains usable. Geometry building and exports are unaffected.

Regression coverage checks that markers stay visible when faces appear during Turbo, can be hidden independently, can be restored while paused, and retain the chosen setting after reopening. The focused playback, preferences, camera, control-net, and highlight suite passed (43 tests).

Rerun `./rundebug.sh` and leave Show positions enabled. Markers should remain present past the first polyline around step 35,000. This change does not address grid scaling; it also renders more markers than the old automatic hiding policy, so compare later playback timings before deciding on further batching work.
