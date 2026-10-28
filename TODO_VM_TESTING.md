# VM testing before public release

Test from a fresh clone in VirtualBox on several clean Linux distributions, including Debian and Ubuntu. Record the distribution and version, desktop session (X11 or Wayland), and any packages or settings needed.

- [ ] Clone into `~/apps/steposcope`, enter the directory, and follow the README as a new user.
- [ ] Run `./1.check_requirements.sh` with uv missing and, where practical, with Python missing or older than 3.12. Confirm the guidance is clear and the checker makes no changes.
- [ ] Install uv using its documented instructions, run the checker again, and run `uv sync`. Check whether uv can provide Python when needed and note disk usage.
- [ ] Launch `./runwl.sh` and open a bundled STEP file. Check the viewport, playback, interaction, and report.
- [ ] Run a headless render from the README and inspect the output.
- [ ] Identify missing Qt, VTK, OpenGL, or XWayland system dependencies and update the Linux instructions with distribution-specific steps where needed.
- [ ] Repeat the launch after a reboot and check that setup is not required again.

VirtualBox graphics can behave differently from physical hardware, so also smoke-test on a physical Linux machine where possible. Record failures and fixes before release.
