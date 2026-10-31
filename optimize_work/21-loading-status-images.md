# Loading feedback using the supplied images

Uses `media/waiting.png` and `media/result.png` in a small floating status window at the bottom-right of the primary screen. Loading a STEP file and playback are explicitly tracked. After five seconds, show the waiting image. When the work finishes or playback pauses, switch to the result image for four seconds, then hide the window. Short operations do not show either image.

A separate lightweight Python/Qt process owns this window. A normal in-window timer cannot display an image while the main Qt thread is blocked by parsing, rendering, or actor maintenance. The independent process also watches a UI heartbeat and displays waiting feedback if the main event loop stops servicing events for five seconds outside a named operation. It does not make model processing interruptible or move geometry into workers.

Images are scaled with preserved proportions to at most 280×320 pixels. The window appears without activating itself, so Ctrl+P remains directed at the main app. A named failed operation uses an Operation stopped caption rather than the success image. Closing the parent pipe, including when the main process dies, terminates the helper. Optional-feedback startup/write failures do not interrupt model processing.

The original assets remain in media/. Setuptools includes them under share/steposcope/media for installed builds; checkout launches use the source media directory. Offscreen/minimal Qt test runs disable automatic helper launching.

Validation: 41 focused tests passed across feedback state transitions, heartbeat stalls, nested operations, controller messages, helper process lifecycle, playback coalescing/input, duration confirmation, and playback listing. An additional offscreen subprocess smoke test exercised the waiting/result window and clean shutdown. A normal Linux desktop run is still needed to judge placement alongside the main viewport.
