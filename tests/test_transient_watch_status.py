"""瞬时查询失败不应触发盯盘通知。"""

from __future__ import annotations

from app.stores.google_errors import is_transient_status_failure


def test_proxy_refused_is_transient() -> None:
    msg = (
        "代理不可用（连接被拒绝）。请确认本地代理已开启；"
        "若当前不需要代理，可清空 .env 里的 HTTP_PROXY / HTTPS_PROXY 后重试。"
    )
    assert is_transient_status_failure(state="unknown", message=msg)


def test_real_rollout_status_is_not_transient() -> None:
    msg = (
        "production: 10428 (5.1054.4.428) codes=[10428] "
        "status=inProgress rollout=0.05 ← target"
    )
    assert not is_transient_status_failure(state="released", message=msg)


def test_watch_query_failure_is_transient() -> None:
    assert is_transient_status_failure(
        state="unknown",
        message="盯盘查询失败: boom",
    )
