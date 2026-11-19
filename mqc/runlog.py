import json
import secrets
from datetime import UTC, datetime
from pathlib import Path


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


class RunLog:
    def __init__(self, run_id: str, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = directory / f"{run_id}.jsonl"
        self._file = self.path.open("a", encoding="utf-8")

    def event(self, level: str, event: str, **fields) -> None:
        record = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "run_id": self.run_id, "level": level,
                  "event": event, **fields}
        self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
