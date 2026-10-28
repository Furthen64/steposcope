"""Explicit VTK imports — replaces the kitchen-sink ``vtkmodules.all`` import.

Only the classes this project actually uses are pulled in, so the process maps
the small subset of VTK shared objects it needs instead of all ~90 modules
(including the unused viskores libs).

Order matters: the OpenGL2 and FreeType backends are imported after
``vtkRenderingCore`` so the object factories pick them up.
"""

from vtkmodules.vtkCommonCore import (
    VTK_UNSIGNED_CHAR,
    vtkFloatArray,
    vtkIdList,
    vtkPoints,
)
from vtkmodules.vtkCommonDataModel import (
    vtkCellArray,
    vtkImageData,
    vtkPolyData,
    vtkPolyLine,
    vtkTriangle,
)
from vtkmodules.vtkFiltersCore import (
    vtkFeatureEdges,
    vtkGlyph3D,
    vtkPolyDataNormals,
    vtkTriangleFilter,
)
from vtkmodules.vtkFiltersGeneral import vtkContourTriangulator
from vtkmodules.vtkFiltersSources import (
    vtkArrowSource,
    vtkCubeSource,
    vtkLineSource,
    vtkOutlineSource,
)
from vtkmodules.vtkIOImage import vtkPNGWriter
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkBillboardTextActor3D,
    vtkCellPicker,
    vtkPolyDataMapper,
    vtkProperty,
    vtkRenderer,
    vtkRenderWindow,
    vtkTextActor,
    vtkWindowToImageFilter,
)

import vtkmodules.vtkRenderingOpenGL2 as vtkRenderingOpenGL2
import vtkmodules.vtkRenderingFreeType as vtkRenderingFreeType

import sys as _sys
vtk = _sys.modules[__name__]  # so callers keep using `vtk.vtkActor` etc.

__all__ = [
    "VTK_UNSIGNED_CHAR",
    "vtkActor",
    "vtkArrowSource",
    "vtkAxesActor",
    "vtkBillboardTextActor3D",
    "vtkCellArray",
    "vtkCellPicker",
    "vtkContourTriangulator",
    "vtkCubeSource",
    "vtkFeatureEdges",
    "vtkFloatArray",
    "vtkGlyph3D",
    "vtkIdList",
    "vtkImageData",
    "vtkInteractorStyleTrackballCamera",
    "vtkLineSource",
    "vtkOutlineSource",
    "vtkPNGWriter",
    "vtkPoints",
    "vtkPolyData",
    "vtkPolyDataMapper",
    "vtkPolyDataNormals",
    "vtkPolyLine",
    "vtkProperty",
    "vtkRenderer",
    "vtkRenderWindow",
    "vtkTextActor",
    "vtkTriangle",
    "vtkTriangleFilter",
    "vtkWindowToImageFilter",
    "vtkRenderingOpenGL2",
    "vtkRenderingFreeType",
    "vtk",
]
