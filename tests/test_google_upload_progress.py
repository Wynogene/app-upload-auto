"""Resumable Google media upload progress logging (offline)."""

from __future__ import annotations

from googleapiclient.http import MediaUploadProgress

from app.stores.google import (
    execute_resumable_media_upload,
    is_retryable_media_upload_error,
    list_edit_bundle_version_codes,
)


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


def test_execute_resumable_stall_raises(monkeypatch) -> None:
    class _StallRequest:
        def next_chunk(self, num_retries: int = 0):  # noqa: ARG002
            return MediaUploadProgress(1_000_000, 10_000_000), None

    monkeypatch.setattr("app.stores.google.logger", type("L", (), {"info": lambda *a, **k: None})())
    # 人为把「上次进度时间」拨到过去：通过极短 stall + 同进度多次调用
    import time

    real_time = time.time

    class _Clock:
        def __init__(self) -> None:
            self.t = real_time()

        def __call__(self) -> float:
            self.t += 60
            return self.t

    clock = _Clock()
    monkeypatch.setattr("time.time", clock)
    try:
        execute_resumable_media_upload(
            _StallRequest(),
            total_size=10_000_000,
            label="aab",
            stall_timeout_seconds=90,
        )
        assert False, "expected stall error"
    except RuntimeError as exc:
        assert "无字节进度" in str(exc)


def test_is_retryable_media_upload_error() -> None:
    assert is_retryable_media_upload_error(RuntimeError("判定卡住；无字节进度"))
    assert is_retryable_media_upload_error(RuntimeError("connection reset by peer"))
    assert not is_retryable_media_upload_error(RuntimeError("permission denied"))


def test_list_edit_bundle_version_codes() -> None:
    class _Bundles:
        def list(self, **_kwargs):
            return self

        def execute(self, **_kwargs):
            return {"bundles": [{"versionCode": 12}, {"versionCode": "13"}]}

    class _Edits:
        def bundles(self):
            return _Bundles()

    class _Service:
        def edits(self):
            return _Edits()

    assert list_edit_bundle_version_codes(_Service(), "pkg", "edit1") == {"12", "13"}
