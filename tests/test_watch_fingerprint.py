"""盯盘运营指纹：放量不变则视为未变化。"""

from __future__ import annotations

from app.core.watch_fingerprint import (
    is_ops_fingerprint,
    ops_watch_fingerprint,
    synthesize_message_from_ops_fp,
)
from app.models import Platform, ReviewState, ReviewStatus


def _android(msg: str, state: ReviewState = ReviewState.RELEASED) -> ReviewStatus:
    return ReviewStatus(
        app_id="blurams",
        platform=Platform.ANDROID,
        state=state,
        message=msg,
    )


def _ios(msg: str, state: ReviewState = ReviewState.RELEASED) -> ReviewStatus:
    return ReviewStatus(
        app_id="blurams",
        platform=Platform.IOS,
        state=state,
        message=msg,
    )


def test_android_same_rollout_same_fp_despite_noise() -> None:
    a = _android(
        "lifecycle: 查询失败 SSL blah\n"
        "production: 5.1 codes=[1957] status=inProgress rollout=0.05 ← target\n"
        "beta: old codes=[1] status=completed\n"
        "进度以 Play Console 为准；很长提示"
    )
    b = _android(
        "lifecycle[production]: PUBLISHED (已上架) codes=[1957]\n"
        "production: 5.1 codes=[1957] status=inProgress rollout=0.05 ← target\n"
        "internal: x codes=[2] status=completed"
    )
    assert ops_watch_fingerprint(a) == ops_watch_fingerprint(b)
    assert is_ops_fingerprint(ops_watch_fingerprint(a))


def test_android_rollout_change_changes_fp() -> None:
    a = _android(
        "production: x codes=[1] status=inProgress rollout=0.05 ← target"
    )
    b = _android(
        "production: x codes=[1] status=inProgress rollout=0.25 ← target"
    )
    assert ops_watch_fingerprint(a) != ops_watch_fingerprint(b)


def test_ios_same_percent_same_fp() -> None:
    # 第4天与「同一百分比」应一致；原文其它字段变化不影响
    a = _ios("5.1：已上线；分批：ACTIVE 第4天≈10%（分批进行中）；开始于2026-09-14")
    b = _ios("5.1：已上线；构建 5.1.1：可用；分批：ACTIVE 第4天≈10%（分批进行中）")
    assert ops_watch_fingerprint(a) == ops_watch_fingerprint(b)


def test_ios_percent_change_changes_fp() -> None:
    a = _ios("分批：ACTIVE 第3天≈5%")
    b = _ios("分批：ACTIVE 第4天≈10%")
    assert ops_watch_fingerprint(a) != ops_watch_fingerprint(b)


def test_synthesize_android_for_titles() -> None:
    fp = ops_watch_fingerprint(
        _android("production: x codes=[1] status=inProgress rollout=0.2 ← target")
    )
    synth = synthesize_message_from_ops_fp(fp)
    assert "rollout=0.2" in synth
    assert "inprogress" in synth.lower() or "inProgress" in synth


def test_terminal_full_release_android_completed() -> None:
    from app.core.watch_fingerprint import is_terminal_full_release_fp

    fp = ops_watch_fingerprint(
        _android("production: x codes=[1] status=completed ← target")
    )
    assert fp == "ops_v1|released|android|completed|-"
    assert is_terminal_full_release_fp(fp)
    # 分批中非终态
    mid = ops_watch_fingerprint(
        _android("production: x codes=[1] status=inProgress rollout=0.05 ← target")
    )
    assert not is_terminal_full_release_fp(mid)


def test_terminal_full_release_ios_complete() -> None:
    from app.core.watch_fingerprint import is_terminal_full_release_fp

    fp = ops_watch_fingerprint(
        _ios("5.1：已上线；分批：COMPLETE（分批已结束（全量））")
    )
    assert "COMPLETE" in fp
    assert is_terminal_full_release_fp(fp)
    mid = ops_watch_fingerprint(_ios("分批：ACTIVE 第4天≈10%"))
    assert not is_terminal_full_release_fp(mid)


def test_terminal_rejects_legacy_fingerprint() -> None:
    from app.core.watch_fingerprint import is_terminal_full_release_fp

    assert not is_terminal_full_release_fp(
        "released|production: completed 很长旧指纹"
    )
