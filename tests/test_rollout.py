"""分阶段发布（staged rollout）参数解析与防呆逻辑测试。"""

from __future__ import annotations

import pytest

from app.core.rollout import (
    BUILTIN_DEFAULT_PERCENT,
    RolloutSpecError,
    compare_rollout,
    describe_rollout,
    is_staged,
    parse_rollout_percent,
    release_status_for,
    resolve_default_rollout_percent,
    resolve_rollout_fraction,
    validate_rollout_track,
)
from app.models import Platform, SubmitRequest, UploadRequest


class TestParseRolloutPercent:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("10", 0.1),
            ("10%", 0.1),
            (10, 0.1),
            ("0.5", 0.005),
            ("50", 0.5),
            ("99.9", 0.999),
            ("100", 1.0),
            ("100%", 1.0),
            ("5", 0.05),
        ],
    )
    def test_parses(self, raw, expected):
        assert parse_rollout_percent(raw) == expected

    @pytest.mark.parametrize("raw", ["0", "-5", "101", "abc", "十"])
    def test_rejects(self, raw):
        with pytest.raises(RolloutSpecError):
            parse_rollout_percent(raw)


class TestResolveDefault:
    def test_apps_yaml_overrides(self):
        assert resolve_default_rollout_percent({"android": {"rollout_percent_default": 5}}) == 5.0
        assert resolve_default_rollout_percent({"android": {"rollout_percent_default": "10%"}}) == 10.0

    def test_empty_string_disables_default(self):
        assert resolve_default_rollout_percent({"android": {"rollout_percent_default": ""}}) is None

    def test_builtin_when_settings_default(self, monkeypatch):
        from app import config as cfg

        monkeypatch.setattr(
            cfg,
            "get_settings",
            lambda: type("S", (), {"rollout_percent_default": "5"})(),
        )
        assert resolve_default_rollout_percent({}) == 5.0
        assert BUILTIN_DEFAULT_PERCENT == 5.0


class TestResolveRolloutFraction:
    def test_explicit_wins(self):
        frac, src = resolve_rollout_fraction(
            explicit=0.2,
            track="production",
            app_cfg={"android": {"rollout_percent_default": 5}},
        )
        assert frac == 0.2
        assert src == "explicit"

    def test_production_uses_default_5(self):
        frac, src = resolve_rollout_fraction(
            explicit=None,
            track="production",
            app_cfg={"android": {"rollout_percent_default": 5}},
        )
        assert frac == 0.05
        assert src == "default"

    def test_internal_ignores_default(self):
        frac, src = resolve_rollout_fraction(
            explicit=None,
            track="internal",
            app_cfg={"android": {"rollout_percent_default": 5}},
        )
        assert frac is None
        assert src is None

    def test_draft_ignores_default(self):
        frac, src = resolve_rollout_fraction(
            explicit=None,
            track="production",
            app_cfg={"android": {"rollout_percent_default": 5}},
            release_status="draft",
        )
        assert frac is None
        assert src is None

    def test_halted_ignores_default(self):
        frac, src = resolve_rollout_fraction(
            explicit=None,
            track="production",
            app_cfg={"android": {"rollout_percent_default": 5}},
            release_status="halted",
        )
        assert frac is None
        assert src is None

    def test_empty_config_means_full_release(self):
        frac, src = resolve_rollout_fraction(
            explicit=None,
            track="production",
            app_cfg={"android": {"rollout_percent_default": ""}},
        )
        assert frac is None
        assert src is None

    def test_default_100_means_full(self):
        frac, src = resolve_rollout_fraction(
            explicit=None,
            track="production",
            app_cfg={"android": {"rollout_percent_default": 100}},
        )
        assert frac == 1.0
        assert src == "default"
        assert is_staged(frac) is False


class TestIsStaged:
    @pytest.mark.parametrize(
        ("fraction", "expected"),
        [
            (None, False),
            (0.05, True),
            (0.1, True),
            (0.5, True),
            (0.999, True),
            (1.0, False),  # 100% 视为全面发布
            (0.0, False),
            (1.5, False),
        ],
    )
    def test_is_staged(self, fraction, expected):
        assert is_staged(fraction) is expected


class TestReleaseStatusFor:
    def test_staged_uses_inprogress(self):
        assert release_status_for(0.05) == "inprogress"
        assert release_status_for(0.1) == "inprogress"
        assert release_status_for(0.99) == "inprogress"

    def test_full_uses_completed(self):
        assert release_status_for(None) == "completed"
        assert release_status_for(1.0) == "completed"


class TestValidateRolloutTrack:
    def test_staged_on_production_ok(self):
        assert validate_rollout_track(0.05, "production") is None
        assert validate_rollout_track(0.1, "production") is None

    @pytest.mark.parametrize("track", ["internal", "alpha", "beta"])
    def test_staged_on_test_track_rejected(self, track):
        err = validate_rollout_track(0.1, track)
        assert err is not None
        assert "production" in err

    def test_full_rollout_any_track_ok(self):
        for track in ("internal", "alpha", "beta", "production"):
            assert validate_rollout_track(None, track) is None
            assert validate_rollout_track(1.0, track) is None


class TestDescribeRollout:
    def test_full(self):
        assert describe_rollout(None) == "全面发布"

    def test_explicit_full(self):
        assert "100" in describe_rollout(1.0)

    def test_staged_no_trailing_zeros(self):
        assert describe_rollout(0.05) == "分阶段发布 5%"
        assert describe_rollout(0.1) == "分阶段发布 10%"
        assert describe_rollout(0.25) == "分阶段发布 25%"
        assert describe_rollout(0.075) == "分阶段发布 7.5%"


class TestCompareRollout:
    def test_increase(self):
        assert compare_rollout(0.1, 0.2) == 1
        assert compare_rollout(0.1, 1.0) == 1

    def test_same(self):
        assert compare_rollout(0.1, 0.1) == 0
        assert compare_rollout(None, None) == 0

    def test_decrease_blocked(self):
        assert compare_rollout(0.5, 0.2) == -1
        # 已全量 -> 不能退回分批
        assert compare_rollout(None, 0.1) == -1


class TestRequestModels:
    def test_upload_request_has_rollout(self):
        req = UploadRequest(app_id="easelife", platform=Platform.ANDROID, rollout_fraction=0.1)
        assert req.rollout_fraction == 0.1

    def test_upload_request_default_none(self):
        req = UploadRequest(app_id="easelife", platform=Platform.ANDROID)
        assert req.rollout_fraction is None

    def test_submit_request_has_rollout(self):
        req = SubmitRequest(
            app_id="easelife",
            platform=Platform.ANDROID,
            version_code="10428",
            track="production",
            rollout_fraction=0.2,
        )
        assert req.rollout_fraction == 0.2
