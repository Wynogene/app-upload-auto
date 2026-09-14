"""iOS 侧测试：状态映射、版本比较、IPA 解析、上传前校验（全部离线，不需要网络与真实密钥）。"""

from __future__ import annotations

import plistlib
import zipfile
from pathlib import Path

import pytest

from app.core.versions import compare_versions, is_version_greater, parse_version_tuple
from app.models import ReviewState
from app.stores.apple_preflight import (
    check_ipa_preflight,
    format_issues,
    has_errors,
)
from app.stores.apple_states import (
    describe_version_state,
    is_live_or_approved,
    is_under_review,
    map_build_processing_state,
    map_version_state,
    pick_version_state,
)
from app.stores.ipa_meta import IpaParseError, parse_ipa_meta

# ---------------- 版本比较 ----------------


def test_parse_version_tuple() -> None:
    assert parse_version_tuple("5.1049.125") == (5, 1049, 125)
    assert parse_version_tuple("5.1054.48.1") == (5, 1054, 48, 1)
    assert parse_version_tuple(None) == ()
    assert parse_version_tuple("1.2beta") == (1, 2)


def test_compare_versions_different_segments() -> None:
    # 关键：4 段 vs 3 段不能按字符串比，否则 "5.1054.48.1" < "5.1054.5" 会被判错
    assert compare_versions("5.1049.126", "5.1049.125") == 1
    assert compare_versions("5.1049.125", "5.1049.125") == 0
    assert compare_versions("5.1049.124", "5.1049.125") == -1
    assert compare_versions("5.1054.48.1", "5.1054.48") == 1
    assert compare_versions("5.1054.51", "5.1054.48.1") == 1
    # 段数不同时短的补 0
    assert compare_versions("1.0", "1.0.0") == 0


def test_is_version_greater_no_baseline() -> None:
    assert is_version_greater("1.0", None) is True
    assert is_version_greater("5.1049.125", "5.1049.125") is False
    assert is_version_greater("5.1049.126", "5.1049.125") is True


# ---------------- 状态映射 ----------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("WAITING_FOR_REVIEW", ReviewState.WAITING_FOR_REVIEW),
        ("IN_REVIEW", ReviewState.IN_REVIEW),
        ("READY_FOR_SALE", ReviewState.RELEASED),
        ("READY_FOR_DISTRIBUTION", ReviewState.RELEASED),
        ("PENDING_DEVELOPER_RELEASE", ReviewState.APPROVED),
        ("PREPARE_FOR_SUBMISSION", ReviewState.DRAFT),
        ("REJECTED", ReviewState.REJECTED),
        ("METADATA_REJECTED", ReviewState.REJECTED),
        ("INVALID_BINARY", ReviewState.REJECTED),
        ("DEVELOPER_REJECTED", ReviewState.CANCELED),
        ("REMOVED_FROM_SALE", ReviewState.HALTED),
        (None, ReviewState.UNKNOWN),
    ],
)
def test_map_version_state(raw, expected) -> None:
    assert map_version_state(raw) == expected


def test_pick_version_state_prefers_new_field() -> None:
    # 新字段 appVersionState 优先于旧字段 appStoreState
    assert pick_version_state({"appVersionState": "IN_REVIEW", "appStoreState": "READY_FOR_SALE"}) == "IN_REVIEW"
    assert pick_version_state({"appStoreState": "READY_FOR_SALE"}) == "READY_FOR_SALE"
    assert pick_version_state({}) is None


def test_unknown_state_falls_back_to_raw_text() -> None:
    # 未知枚举值不能被吞掉，要原样透出便于排查
    assert describe_version_state("SOME_NEW_STATE") == "SOME_NEW_STATE"
    assert describe_version_state(None) == "未知状态"


def test_under_review_and_live_helpers() -> None:
    assert is_under_review("WAITING_FOR_REVIEW") is True
    assert is_under_review("IN_REVIEW") is True
    assert is_under_review("READY_FOR_SALE") is False
    assert is_live_or_approved("READY_FOR_SALE") is True
    assert is_live_or_approved("PENDING_DEVELOPER_RELEASE") is True
    assert is_live_or_approved("IN_REVIEW") is False


def test_build_processing_state() -> None:
    assert map_build_processing_state("VALID") == ReviewState.APPROVED
    assert map_build_processing_state("PROCESSING") == ReviewState.IN_REVIEW
    assert map_build_processing_state("INVALID") == ReviewState.REJECTED
    assert map_build_processing_state("FAILED") == ReviewState.REJECTED


# ---------------- IPA 解析 ----------------


def _make_ipa(tmp_path: Path, info: dict, app_dir: str = "MyApp.app") -> Path:
    ipa = tmp_path / "test.ipa"
    with zipfile.ZipFile(ipa, "w") as zf:
        zf.writestr(
            f"Payload/{app_dir}/Info.plist",
            plistlib.dumps(info, fmt=plistlib.FMT_BINARY),
        )
    return ipa


def test_parse_ipa_meta_binary_plist(tmp_path: Path) -> None:
    ipa = _make_ipa(
        tmp_path,
        {
            "CFBundleIdentifier": "com.blurams.ipc",
            "CFBundleShortVersionString": "5.1049.125",
            "CFBundleVersion": "5.1049.125.3",
            "CFBundleDisplayName": "blurams",
            "CFBundlePackageType": "APPL",
        },
    )
    meta = parse_ipa_meta(ipa)
    assert meta.bundle_id == "com.blurams.ipc"
    assert meta.version_name == "5.1049.125"
    assert meta.build_number == "5.1049.125.3"
    assert meta.app_name == "blurams"


def test_parse_ipa_meta_xml_plist(tmp_path: Path) -> None:
    ipa = _make_ipa(
        tmp_path,
        {
            "CFBundleIdentifier": "com.vitec.easelifeEn",
            "CFBundleShortVersionString": "5.1054.52",
            "CFBundleVersion": "5.1054.52.1",
        },
    )
    assert parse_ipa_meta(ipa).bundle_id == "com.vitec.easelifeEn"


def test_parse_ipa_ignores_nested_watch_plist(tmp_path: Path) -> None:
    """Watch 扩展里有嵌套 Info.plist，必须只认顶层主 App 的。"""
    ipa = tmp_path / "nested.ipa"
    with zipfile.ZipFile(ipa, "w") as zf:
        zf.writestr(
            "Payload/MyApp.app/Watch/Watch.app/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.blurams.ipc.watchkitapp",
                    "CFBundleShortVersionString": "9.9.9",
                    "CFBundleVersion": "9",
                    "CFBundlePackageType": "APPL",
                },
                fmt=plistlib.FMT_BINARY,
            ),
        )
        zf.writestr(
            "Payload/MyApp.app/Info.plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.blurams.ipc",
                    "CFBundleShortVersionString": "5.1049.125",
                    "CFBundleVersion": "5.1049.125.3",
                    "CFBundlePackageType": "APPL",
                },
                fmt=plistlib.FMT_BINARY,
            ),
        )
    meta = parse_ipa_meta(ipa)
    assert meta.bundle_id == "com.blurams.ipc"
    assert meta.version_name == "5.1049.125"


def test_parse_ipa_missing_fields(tmp_path: Path) -> None:
    ipa = _make_ipa(
        tmp_path,
        {"CFBundleIdentifier": "com.x", "CFBundleShortVersionString": "1.0"},
    )
    with pytest.raises(IpaParseError, match="CFBundleVersion"):
        parse_ipa_meta(ipa)


def test_parse_ipa_not_a_ipa(tmp_path: Path) -> None:
    bad = tmp_path / "bad.ipa"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("readme.txt", "not an ipa")
    with pytest.raises(IpaParseError, match="Info.plist"):
        parse_ipa_meta(bad)


def test_parse_ipa_missing_file(tmp_path: Path) -> None:
    with pytest.raises(IpaParseError,match="不存在"):
        parse_ipa_meta(tmp_path / "nope.ipa")


# ---------------- 上传前校验 ----------------


def _meta(bundle="com.blurams.ipc", version="5.1049.126", build="5.1049.126.1"):
    from app.stores.ipa_meta import IpaMeta

    return IpaMeta(bundle_id=bundle, version_name=version, build_number=build)


def test_preflight_clean_pass() -> None:
    issues = check_ipa_preflight(
        _meta(),
        expected_bundle_id="com.blurams.ipc",
        latest_released_version="5.1049.125",
        existing_build_versions={"5.1049.125.3"},
    )
    assert issues == []
    assert has_errors(issues) is False
    assert format_issues(issues) == ""


def test_preflight_bundle_mismatch_is_blocking() -> None:
    issues = check_ipa_preflight(
        _meta(bundle="com.vitec.easelifeEn"),
        expected_bundle_id="com.blurams.ipc",
    )
    assert has_errors(issues) is True
    assert "拿错包" in format_issues(issues)


def test_preflight_version_not_bumped_is_blocking() -> None:
    issues = check_ipa_preflight(
        _meta(version="5.1049.125"),
        latest_released_version="5.1049.125",
    )
    assert has_errors(issues) is True
    assert any(i.code == "version_not_bumped" for i in issues)


def test_preflight_duplicate_build_is_blocking() -> None:
    issues = check_ipa_preflight(
        _meta(build="5.1049.125.3"),
        existing_build_versions={"5.1049.125.3"},
    )
    assert has_errors(issues) is True
    assert any(i.code == "build_duplicate" for i in issues)


def test_preflight_in_review_is_only_warning() -> None:
    issues = check_ipa_preflight(
        _meta(version="5.1049.126"),
        latest_released_version="5.1049.125",
        versions_in_review={"5.1049.126"},
    )
    assert has_errors(issues) is False
    assert any(i.code == "already_in_review" for i in issues)
    assert "提示" in format_issues(issues)


def test_preflight_no_draft_hint_for_released_version() -> None:
    """版本已上线时，不应再提示「ASC 中存在预留草稿」——上面已报版本未递增，重复只会误导。"""
    issues = check_ipa_preflight(
        _meta(version="5.1049.125", build="5.1049.125.9"),
        latest_released_version="5.1049.125",
        released_versions={"5.1049.125"},
        version_strings={"5.1049.125"},
    )
    codes = {i.code for i in issues}
    assert codes == {"version_not_bumped"}
    assert "草稿" not in format_issues(issues)


def test_preflight_hints_draft_record_when_not_released() -> None:
    """真正为提审预留的草稿记录（未上线、未在审）才应该提示。"""
    issues = check_ipa_preflight(
        _meta(version="5.1049.126", build="5.1049.126.1"),
        latest_released_version="5.1049.125",
        released_versions={"5.1049.125"},
        version_strings={"5.1049.125", "5.1049.126"},
    )
    assert has_errors(issues) is False
    assert any(i.code == "version_record_exists" for i in issues)


def test_preflight_multiple_errors_all_reported() -> None:
    """一次把所有问题都报出来，别让用户来回试。"""
    issues = check_ipa_preflight(
        _meta(bundle="com.wrong.app", version="5.1049.125", build="5.1049.125.3"),
        expected_bundle_id="com.blurams.ipc",
        latest_released_version="5.1049.125",
        existing_build_versions={"5.1049.125.3"},
    )
    codes = {i.code for i in issues if i.is_error}
    assert codes == {"bundle_mismatch", "version_not_bumped", "build_duplicate"}
    assert "3 项阻断" in format_issues(issues)
