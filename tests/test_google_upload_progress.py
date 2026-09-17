"""Resumable Google media upload progress logging (offline)."""

from __future__ import annotations

from googleapiclient.http import MediaUploadProgress

from app.stores.google import execute_resumable_media_upload


class _FakeRequest:
    def __init__(self, total: int, chunk: int) -> None:
        self.total = total
        self.chunk = chunk
        self.sent = 0

    def next_chunk(self, num_retries: int = 0):  # noqa: ARG002
        if self.sent >= self.total:
            return None, {"versionCode": 1}
        self.sent = min(self.total, self.sent + self.chunk)
        progress = MediaUploadProgress(self.sent, self.total)
        if self.sent >= self.total:
            return progress, {"versionCode": 42}
        return progress, None


def test_execute_resumable_media_upload_logs_and_returns(monkeypatch) -> None:
    logs: list[str] = []

    class _L:
        def info(self, msg, *args):  # noqa: ANN001
            logs.append(msg.format(*args) if args else str(msg))

    monkeypatch.setattr("app.stores.google.logger", _L())
    total = 24 * 1024 * 1024
    out = execute_resumable_media_upload(
        _FakeRequest(total, 8 * 1024 * 1024),
        total_size=total,
        label="aab",
    )
    assert out == {"versionCode": 42}
    joined = "\n".join(logs)
    assert "upload start" in joined
    assert "upload progress" in joined
    assert "upload complete" in joined
    assert "MB" in joined
