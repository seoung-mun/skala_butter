"""Append-only CSV event store, shared by the API process and (optionally) a separate worker process."""
import csv
import fcntl
import json
import os
import sys
import threading
from pathlib import Path

csv.field_size_limit(sys.maxsize)


class ProcessLock:
    """Re-entrant inside a process (RLock) and exclusive across processes (flock on `.store.lock`), so an API
    container and a worker container can append to the same tables without interleaving."""

    def __init__(self, path):
        self._thread, self._path, self._depth, self._file = threading.RLock(), path, 0, None

    def __enter__(self):
        self._thread.acquire()
        if self._depth == 0:
            self._file = self._file or open(self._path, "a")
            fcntl.flock(self._file, fcntl.LOCK_EX)
        self._depth += 1
        return self

    def __exit__(self, *exc):
        self._depth -= 1
        if self._depth == 0:
            fcntl.flock(self._file, fcntl.LOCK_UN)
        self._thread.release()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = ProcessLock(self.root/".store.lock")

    def path(self, name):
        if not name.replace("_", "").isalnum():
            raise ValueError("Invalid table name")
        return self.root / f"{name}.csv"

    def append(self, name, payload):
        with self.lock:
            path = self.path(name)
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                if new:
                    writer.writerow(["payload"])
                writer.writerow([json.dumps(payload, ensure_ascii=False, allow_nan=False)])
                file.flush()
                os.fsync(file.fileno())

    def read(self, name):
        with self.lock:
            path = self.path(name)
            if not path.exists():
                return []
            try:
                with path.open(newline="", encoding="utf-8") as file:
                    reader = csv.DictReader(file)
                    if reader.fieldnames != ["payload"]:
                        raise ValueError("Expected payload column")
                    return [json.loads(row["payload"]) for row in reader]
            except (ValueError, KeyError, csv.Error) as error:
                raise ValueError(f"Corrupt CSV table {name}: {error}") from error

    def latest(self, name):
        """Last record by reading the file tail only: payloads are one-line JSON (newlines are escaped).

        Parsing the whole table here made monitoring quadratic in history (profiled: 98 of 111 s per experiment).
        """
        with self.lock:
            path = self.path(name)
            if not path.exists():
                return None
            with path.open("rb") as file:
                position, chunk = file.seek(0, os.SEEK_END), b""
                while position > 0 and b"\n" not in chunk.rstrip(b"\r\n"):
                    step = min(1 << 16, position)
                    position -= step
                    file.seek(position)
                    chunk = file.read(step)+chunk
        lines = chunk.rstrip(b"\r\n").splitlines()
        if position == 0 and len(lines) < 2:
            return None  # header only
        try:
            payload, = next(csv.reader([lines[-1].decode("utf-8")]))
            return json.loads(payload)
        except (ValueError, csv.Error) as error:
            raise ValueError(f"Corrupt CSV table {name}: {error}") from error
