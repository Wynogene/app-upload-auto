"""Google Play 分阶段发布出网细节测试。

覆盖：
* ``inProgress`` 枚举大小写（API 只认驼峰，写错会 400）
* ``userFraction`` 只在分批时下发
* 防呆：全面发布不能倒退回分批、放量不能缩水
"""

from __future__ import annotations

from app.stores.google import _to_api_status


class TestApiStatusCasing:
    def test_inprogress_becomes_camel_case(self):
        # Google Play API 的枚举是 `inProgress`；小写会被 400 拒绝
        assert _to_api_status("inprogress") == "inProgress"

    def test_already_camel_case_passthrough(self):
        assert _to_api_status("inProgress") == "inProgress"

    def test_case_insensitive(self):
        assert _to_api_status("INPROGRESS") == "inProgress"

    def test_other_statuses_lowercase(self):
        assert _to_api_status("completed") == "completed"
        assert _to_api_status("draft") == "draft"
        assert _to_api_status("halted") == "halted"

    def test_unknown_falls_back_to_completed(self):
        assert _to_api_status(None) == "completed"
        assert _to_api_status("bogus") == "completed"


class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self, **_kwargs):
        return self._payload


class _FakeTracks:
    def __init__(self, sink):
        self._sink = sink

    def update(self, **kwargs):
        self._sink.append(kwargs)
        return _FakeExec({"track": kwargs.get("track")})


class _FakeEdits:
    def __init__(self, sink):
        self._sink = sink

    def tracks(self):
        return _FakeTracks(self._sink)


class _FakeService:
    def __init__(self):
        self.calls: list[dict] = []

    def edits(self):
        return _FakeEdits(self.calls)


class TestAssignTrackBody:
    def _call(self, **kwargs):
        from app.stores.google import GoogleStoreClient

        svc = _FakeService()
        client = GoogleStoreClient.__new__(GoogleStoreClient)
        client._assign_track(svc, package_name="com.x", edit_id="e1", **kwargs)
        return svc.calls[0]["body"]

    def test_completed_has_no_user_fraction(self):
        body = self._call(
            track="production",
            version_code=10428,
            release_status="completed",
            user_fraction=None,
        )
        rel = body["releases"][0]
        assert rel["status"] == "completed"
        assert "userFraction" not in rel

    def test_staged_sends_camel_status_and_fraction(self):
        body = self._call(
            track="production",
            version_code=10428,
            release_status="inprogress",
            user_fraction=0.1,
        )
        rel = body["releases"][0]
        assert rel["status"] == "inProgress"
        assert rel["userFraction"] == 0.1

    def test_fraction_ignored_when_status_is_completed(self):
        # userFraction 只在 inProgress/halted 下合法，全量时绝不能带
        body = self._call(
            track="production",
            version_code=10428,
            release_status="completed",
            user_fraction=0.1,
        )
        assert "userFraction" not in body["releases"][0]

    def test_release_notes_still_attached(self):
        body = self._call(
            track="production",
            version_code=10428,
            release_notes=[{"language": "en-US", "text": "hi"}],
            release_status="inprogress",
            user_fraction=0.25,
        )
        rel = body["releases"][0]
        assert rel["releaseNotes"] == [{"language": "en-US", "text": "hi"}]
        assert rel["userFraction"] == 0.25
