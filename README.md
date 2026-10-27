# Steposcope

**A microscope for STEP files.**

Steposcope is a Linux-based STEP file visualization and analysis tool written in Python, using OpenGL rendering to reveal how STEP geometry is constructed.

Instead of simply displaying the finished CAD model, Steposcope lets you **watch the geometry emerge, one step at a time.**

Open a STEP file, press **Play**, and observe its geometric information being processed and rendered progressively.

### Why Steposcope?

STEP files contain far more than the finished 3D shapes shown by conventional CAD viewers. They describe geometric entities, topology, references, and relationships that aren't always easy to inspect.

Steposcope aims to make that hidden structure visible.

Whether you're investigating malformed geometry, learning about the STEP format, or trying to understand why a CAD model behaves unexpectedly, Steposcope provides a different perspective: **watching the model take shape from its underlying data.**

### Features

- **Progressive visualization** — Play through STEP geometry line by line.
- **Interactive OpenGL canvas** — Observe the model as its geometry is rendered.
- **Geometry inspection** — Explore the information behind the resulting shapes.
- **Debugging and analysis** — Investigate unusual, incomplete, or problematic geometry.
- **STEP AP203 support** — Tested with AP203 STEP files.
- **Linux native** — Built with Python and OpenGL.

### Project status

Steposcope is an experimental tool under active development, focused on making STEP file internals easier to understand and investigate.

### The idea

*A conventional CAD viewer shows you what a STEP file looks like.*

**Steposcope helps you understand how it gets there.**
