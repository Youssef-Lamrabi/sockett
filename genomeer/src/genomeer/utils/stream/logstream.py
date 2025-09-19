# logstream.py
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
import threading, time, uuid, os, tempfile
from typing import Deque, Generator, Optional, TextIO
from pathlib import Path

@dataclass
class LogChunk:
    seq: int
    text: str
    ts: float

class InstallLogStream:
    """
    Thread-safe stream that also persists durable lines to a file so other
    processes can 'tail' them. Ephemeral updates are emitted inline with a tag.
    """
    def __init__(self, maxlen: int = 10000, file_path: Optional[Path] = None):
        self._buf: Deque[LogChunk] = deque(maxlen=maxlen)
        self._seq: int = 0

        self._eph_text: Optional[str] = None
        self._eph_seq: int = 0

        self._cv = threading.Condition()
        self._closed = False

        # file-backing
        self._file_path = Path(file_path) if file_path else None
        self._fp: Optional[TextIO] = None
        if self._file_path:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            # line-buffered text mode so readers see updates immediately
            self._fp = open(self._file_path, "a", encoding="utf-8", buffering=1)

    @property
    def file_path(self) -> Optional[Path]:
        return self._file_path

    # ---- Writers ---------------------------------------------------
    def _write_file_line(self, line: str) -> None:
        if self._fp:
            try:
                self._fp.write(line.rstrip("\n") + "\n")
            except Exception:
                pass  # don't crash producer on I/O hiccups

    def push(self, text: str) -> int:
        """Append a durable log entry."""
        if not text:
            return self._seq
        with self._cv:
            self._seq += 1
            self._buf.append(LogChunk(self._seq, text, time.time()))
            self._write_file_line(text)
            self._cv.notify_all()
            return self._seq

    def replace(self, text: str) -> int:
        """Update the ephemeral line (also emit a tagged line to the file)."""
        with self._cv:
            self._eph_text = text
            self._eph_seq += 1
            # Persist a tagged ephemeral snapshot. External readers can handle specially.
            self._write_file_line(f"[EPH] {text}")
            self._cv.notify_all()
            return self._eph_seq

    def finalize_replace(self) -> Optional[int]:
        """Turn ephemeral into durable."""
        with self._cv:
            if self._eph_text:
                self._seq += 1
                self._buf.append(LogChunk(self._seq, self._eph_text, time.time()))
                self._write_file_line(self._eph_text)
                self._eph_text = None
                self._eph_seq += 1
                self._cv.notify_all()
                return self._seq
            return None

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._write_file_line("<<CLOSED>>")
            self._cv.notify_all()
        if self._fp:
            try: self._fp.close()
            except Exception: pass

    # ---- Readers (unchanged API) ----------------------------------
    def read(self, cursor: int = 0, ephemeral_cursor: int = 0, limit: int = 1000) -> dict:
        with self._cv:
            items = [c for c in self._buf if c.seq > cursor][:limit]
            new_cursor = items[-1].seq if items else cursor
            eph = None
            if self._eph_seq != ephemeral_cursor:
                eph = {"seq": self._eph_seq, "text": self._eph_text}
            return {
                "events": [c.__dict__ for c in items],
                "cursor": new_cursor,
                "ephemeral": eph,
                "closed": self._closed,
            }

    def stream(self, cursor: int = 0, ephemeral_cursor: int = 0, wait_timeout: float = 0.25) -> Generator[dict, None, None]:
        while True:
            with self._cv:
                has_durable = (self._buf and self._buf[-1].seq > cursor)
                has_ephemeral = (self._eph_seq != ephemeral_cursor)
                if not (has_durable or has_ephemeral or self._closed):
                    self._cv.wait(timeout=wait_timeout)
                    continue
                payload = self.read(cursor, ephemeral_cursor)
                cursor = payload["cursor"]
                if payload["ephemeral"] is not None:
                    ephemeral_cursor = payload["ephemeral"]["seq"]
                closed = payload["closed"]
            yield payload
            if closed:
                break


class LogRegistry:
    def __init__(self, base_dir: Optional[Path] = None):
        # store live streams here
        self._by_id: dict[str, InstallLogStream] = {}
        self._lock = threading.Lock()
        # temp folder to persist logs
        self.base_dir = Path(base_dir) if base_dir else Path(tempfile.gettempdir()) / "bioagent-logs"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, sid: str) -> Path:
        return self.base_dir / f"{sid}.log"

    def create(self) -> tuple[str, InstallLogStream]:
        sid = uuid.uuid4().hex
        stream = InstallLogStream(file_path=self._path_for(sid))
        with self._lock:
            self._by_id[sid] = stream
        return sid, stream

    def get(self, sid: str) -> InstallLogStream:
        # Same-process lookup (original behavior)
        return self._by_id[sid]

    # Optional: attach from another process by reading the file only
    def attach_reader(self, sid: str) -> Path:
        """
        Return the path to the persisted log so external processes can tail it.
        """
        return self._path_for(sid)

    def close(self, sid: str) -> None:
        with self._lock:
            s = self._by_id.pop(sid, None)
        if s:
            s.close()
