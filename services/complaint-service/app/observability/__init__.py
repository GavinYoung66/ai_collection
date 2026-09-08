"""observability package — stretch hooks only (not Batch-D complete)."""
from app.observability.call_log_sink import (
    CallLogSink,
    TempFileCallLogSink,
    emit_call_log,
    get_call_log_sink,
    set_call_log_sink,
)

__all__ = [
    "CallLogSink",
    "TempFileCallLogSink",
    "emit_call_log",
    "get_call_log_sink",
    "set_call_log_sink",
]
