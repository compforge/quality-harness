"""Dataset type aliases; all dependency and execution semantics are shared."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from trace_harness.dataset import Dataset
from trace_harness.detectors import Detect, Detector, DetectorResult
from trace_harness.detectors import plan_detectors as plan_detectors

if TYPE_CHECKING:
    from trace_harness.batch import BatchContext

BatchDetector: TypeAlias = Detector[Dataset, "BatchContext"]
BatchDetect: TypeAlias = Detect[Dataset, "BatchContext"]

BatchDetectorResult = DetectorResult
