"""与生产决策链隔离的 point-in-time 足球情报采集组件。"""

from app.intelligence.adapters import (
    FIVE_LEAGUES,
    ManualObservationAdapter,
    MetNoWeatherAdapter,
    OpenFootballJsonAdapter,
)
from app.intelligence.collection import (
    ContentAddressedArchive,
    JsonlObservationStore,
    SourceHealthTracker,
)
from app.intelligence.contracts import (
    CaptureWindow,
    DataType,
    Observation,
    RawPayload,
    SourcePolicy,
)
from app.intelligence.planning import due_capture_window

__all__ = [
    "CaptureWindow",
    "ContentAddressedArchive",
    "DataType",
    "FIVE_LEAGUES",
    "JsonlObservationStore",
    "ManualObservationAdapter",
    "MetNoWeatherAdapter",
    "Observation",
    "OpenFootballJsonAdapter",
    "RawPayload",
    "SourceHealthTracker",
    "SourcePolicy",
    "due_capture_window",
]
