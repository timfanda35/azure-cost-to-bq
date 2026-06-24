from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ObjectMeta:
    key: str
    last_modified: datetime
    size: int = 0


@dataclass(frozen=True)
class ExportRun:
    """A single completed Cost Management export run, discovered from its manifest.

    The presence of a manifest is the readiness gate: a run folder without a
    manifest is in-progress or failed and must be ignored. ``blobs`` is the
    authoritative list of data part files taken from the manifest's ``blobs[]``.
    """
    run_id: str
    submitted_time: datetime
    data_version: str
    blobs: list[str] = field(default_factory=list)
