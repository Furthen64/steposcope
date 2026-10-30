# Floating preview of the last paused view

Resuming playback captures the existing VTK framebuffer before confirmation or mode changes can hide the paused markers. The capture is encoded as PNG in memory and displayed in a bottom-right overlay titled **Last paused view · step N**. No screenshot files are written.

The outer panel occupies approximately 15% of the viewport area (39% of each dimension), with a 14-pixel margin. Its image preserves the source aspect ratio and scales when the viewport resizes. The panel ignores mouse input. It hides on pause, updates on the next resume, and clears when another file opens.

This is a static reference image, not another renderer. Capture occurs only on play/resume; there is no per-step capture or extra render request. The PNG reads VTK's framebuffer directly, excluding Qt overlays and avoiding recursive screenshots of the preview itself. The diagnostic logger attributes capture cost to `viewport.capture_preview`. If the viewport has not rendered yet, the panel stays hidden.

Validation: 27 focused tests passed. A real offscreen VTK render verifies PNG capture size and pixels. Integration coverage checks panel placement/area, static contents during playback, resizing, pause visibility, replacement on resume, and clearing on file open. Playback input and duration-warning tests also passed.

Run `./rundebug.sh`, pause to inspect/rotate the model, then resume using Ctrl+P. The paused view should remain visible in the corner even if the live preview hides markers.
