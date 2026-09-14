"""IPA 上传前校验（纯逻辑，不依赖网络，便于单测）。

目的与 Android 侧一致：**在传几百 MB 之前就拦下必然失败的包**。
Apple 的失败往往要等整个文件传完再等处理，代价比 Google 还高。

三道硬校验（error，直接拦下）：
  1. IPA 的 bundle id 与 apps.yaml 配置不一致
  2. CFBundleShortVersionString 没有比线上版本更高
  3. CFBundleVersion 与 ASC 中已有构建重复（Apple 不允许同版本号重复上传）

外加若干提示（warn，只提醒不拦截）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.versions import is_version_greater
from app.stores.ipa_meta import IpaMeta

ERROR = "error"
WARN = "warn"


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    level: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.level == ERROR


def check_ipa_preflight(
    meta: IpaMeta,
    *,
    expected_bundle_id: str | None = None,
    latest_released_version: str | None = None,
    released_versions: set[str] | None = None,
    existing_build_versions: set[str] | None = None,
    version_strings: set[str] | None = None,
    versions_in_review: set[str] | None = None,
) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    existing_build_versions = existing_build_versions or set()
    version_strings = version_strings or set()
    versions_in_review = versions_in_review or set()
    released_versions = released_versions or set()

    # 1) bundle id 校验：防止「拿错包传给另一个 App」
    if expected_bundle_id and meta.bundle_id != expected_bundle_id:
        issues.append(
            PreflightIssue(
                "bundle_mismatch",
                ERROR,
                f"IPA 的 bundle id 是 `{meta.bundle_id}`，"
                f"但 apps.yaml 配置的是 `{expected_bundle_id}`。"
                "这多半是拿错包了——传上去 Apple 会直接拒绝。",
            )
        )

    # 2) 版本号必须严格递增
    if (
        latest_released_version
        and not is_version_greater(meta.version_name, latest_released_version)
    ):
        issues.append(
            PreflightIssue(
                "version_not_bumped",
                ERROR,
                f"IPA 版本号 `{meta.version_name}` 未高于线上版本 "
                f"`{latest_released_version}`。App Store 要求版本号必须递增，"
                "请升版本号后重新打包。",
            )
        )

    # 3) 构建号查重
    if meta.build_number in existing_build_versions:
        issues.append(
            PreflightIssue(
                "build_duplicate",
                ERROR,
                f"构建号 `{meta.build_number}` 在 App Store Connect 中已存在"
                "（Apple 不允许同一构建号重复上传，即使旧构建已过期）。"
                "请提升 CFBundleVersion 后重新打包。",
            )
        )

    # 4) 同一版本号已提审中 —— 避免无意义重复操作
    if meta.version_name in versions_in_review:
        issues.append(
            PreflightIssue(
                "already_in_review",
                WARN,
                f"版本 `{meta.version_name}` 已在审核流程中，"
                "重复提审不会加速，反而可能造成状态混乱。",
            )
        )

    # 5) 版本号已存在于 ASC，但既非已上线、也不在审核中
    #    → 这才是真正「为提审预留的草稿记录」，值得提示。
    #    若该版本已上线，上面的 version_not_bumped 已说明问题，再提示只会造成误导。
    if (
        meta.version_name in version_strings
        and meta.version_name not in versions_in_review
        and meta.version_name not in released_versions
    ):
        issues.append(
            PreflightIssue(
                "version_record_exists",
                WARN,
                f"ASC 中已存在版本记录 `{meta.version_name}`（通常是为提审预留的草稿），"
                "上传构建后会关联到该版本，属正常流程。",
            )
        )

    return issues


def has_errors(issues: list[PreflightIssue]) -> bool:
    return any(i.is_error for i in issues)


def format_issues(issues: list[PreflightIssue]) -> str:
    """把校验结果整理成可读文案；无问题时返回空串。"""
    if not issues:
        return ""
    errors = [i for i in issues if i.is_error]
    warns = [i for i in issues if not i.is_error]
    lines: list[str] = []
    for i in errors:
        lines.append(f"[阻断] {i.message}")
    for i in warns:
        lines.append(f"[提示] {i.message}")
    if errors:
        lines.insert(0, f"上传前校验未通过（{len(errors)} 项阻断）：")
    else:
        lines.insert(0, f"上传前校验通过，但有 {len(warns)} 项提示：")
    return "\n".join(lines)
