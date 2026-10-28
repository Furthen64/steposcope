"""Shared test helpers.

STEP models are discovered generically from the repository corpus (or any
directory listed in the STEPSCOPE_CORPUS environment variable, path-separator
style).  No test hard-wires a checkout layout or a particular model file.
"""

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def corpus_roots():
    roots = [Path(entry) for entry in os.environ.get("STEPSCOPE_CORPUS", "").split(os.pathsep) if entry]
    roots.append(REPO_ROOT / "corpus")
    return roots


@pytest.fixture(scope="session")
def corpus_files():
    """Every STEP file available for exercises, sorted by name.

    Files are deduplicated by name, preferring the shallowest path, so a
    curated copy at the corpus root is swept once even if a subdirectory
    keeps the original.
    """

    found = {}
    for root in corpus_roots():
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".step":
                current = found.get(path.name.lower())
                if current is None or len(path.parts) < len(current.parts):
                    found[path.name.lower()] = path
    return sorted(found.values(), key=lambda path: path.name.lower())


@pytest.fixture(scope="session")
def small_corpus(corpus_files):
    """Corpus files that build quickly (under a few thousand entities)."""

    from step_explorer.step.parser import StepDocument

    selected = []
    for path in corpus_files:
        try:
            document = StepDocument.from_file(path)
        except Exception:
            continue
        if len(document.entities) <= 4000:
            selected.append(path)
    return selected


@pytest.fixture(scope="session")
def corpus_models(small_corpus):
    """(path, document, snapshot) for every small corpus model, built once."""

    from step_explorer.geometry.builder import GeometryBuilder
    from step_explorer.step.parser import parse_step

    models = []
    for path in small_corpus:
        document = parse_step(path.read_text(encoding="utf-8", errors="replace"))
        models.append((path, document, GeometryBuilder(document).build()))
    return models


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp):
    """A fully initialised VtkView, so tests never rely on partial construction."""

    from step_explorer.visualization.vtk_view import VtkView

    widget = VtkView()
    if widget.renderer is None:
        widget.close()
        pytest.skip("VTK is not installed")
    yield widget
    widget.close()
    widget.deleteLater()
