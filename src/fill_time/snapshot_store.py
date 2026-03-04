"""JSONL read/write for full-depth order book snapshots with file rotation."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from .config import FillTimeConfig
from .models import SnapshotRecord

logger = logging.getLogger(__name__)


class SnapshotStore:
    """Persists order book snapshots to JSONL files with rotation."""

    def __init__(self, config: FillTimeConfig):
        self._config = config
        self._dir = Path(config.snapshot_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._current_file: Optional[Path] = None
        self._current_count: int = 0

    def _new_file_path(self) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self._dir / f"snapshots_{ts}.jsonl"

    def _ensure_file(self) -> Path:
        if (
            self._current_file is None
            or self._current_count >= self._config.max_snapshots_per_file
        ):
            self._current_file = self._new_file_path()
            self._current_count = 0
            logger.info(f"Rotating snapshot file to {self._current_file}")
        return self._current_file

    def write(self, record: SnapshotRecord) -> None:
        path = self._ensure_file()
        try:
            with open(path, "a") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
            self._current_count += 1
        except Exception as e:
            logger.warning(f"Failed to write snapshot: {e}")

    def read_file(self, path: Path) -> Iterator[SnapshotRecord]:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        yield SnapshotRecord.from_dict(json.loads(line))
        except Exception as e:
            logger.warning(f"Failed to read {path}: {e}")

    def read_all(self) -> Iterator[SnapshotRecord]:
        for path in sorted(self._dir.glob("snapshots_*.jsonl")):
            yield from self.read_file(path)

    def read_recent(self, max_files: int = 5) -> Iterator[SnapshotRecord]:
        files = sorted(self._dir.glob("snapshots_*.jsonl"), reverse=True)[:max_files]
        for path in reversed(files):  # oldest first
            yield from self.read_file(path)

    def list_files(self) -> List[Path]:
        return sorted(self._dir.glob("snapshots_*.jsonl"))
