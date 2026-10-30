# Viewport navigation locks during playback

Playback now locks viewport mouse and keyboard interaction and displays a notice: **Viewport locked during playback · Space to pause**, with a reminder that pan, rotate, and zoom are available while paused.

Mouse presses, drags, double-click picking, scrolling, touch/gesture input, and viewport keyboard shortcuts are consumed while locked. Shortcut-override events are accepted so Qt actions cannot bypass the lock. Space requests pause; repeated key events are ignored. The viewport receives keyboard focus when playback starts. Reset, Fit, Focus on Face, and projection controls are unavailable during playback.

Pausing, cancelling playback, and reaching the end restore navigation. Entering/leaving the lock clears drag state so an interrupted drag cannot move the camera unexpectedly. Automatic camera fitting and programmatic scene updates still work. The existing Pause button remains available outside the viewport.

The lock does not interrupt a currently running geometry/render callback; Space takes effect when Qt services the event. This change prevents competing navigation work, but does not itself implement the proposed reduction in presentation frequency.

Validation: 44 focused tests passed, including a new integration test that starts playback, verifies mouse/wheel/reset input is blocked, checks Space autorepeat is ignored, pauses with Space, and verifies mouse orbit is available again. Existing playback, camera, picking, and duration-warning tests remain passing.
