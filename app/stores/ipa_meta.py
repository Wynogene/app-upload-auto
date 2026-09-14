"""解析 IPA 元数据（对标 Android 侧的 aab_meta.py）。

IPA 就是一个 zip，结构固定：

    Payload/
      <AppName>.app/
        Info.plist          ← 我们要读的（通常是 binary plist）
        ...

只需要读 `Info.plist` 这一个 entry，不必解压整个几百 MB 的包。
Info.plist 一般是二进制格式，`plistlib.loads` 同时支持二进制与 XML。
"""

from __future__ import annotations

import plistlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Payload/xxx.app/Info.plist —— 只匹配顶层，避免命中 Watch/扩展里的嵌套 Info.plist
_INFO_PLIST_RE = r"^Payload/[^/]+\.app/Info\.plist$"


@dataclass(frozen=True)
class IpaMeta:
    bundle_id: str
    version_name: str  # CFBundleShortVersionString，对应 ASC 的 versionString
    build_number: str  # CFBundleVersion，对应 ASC build 的 version
    app_name: str | None = None
    minimum_os: str | None = None


class IpaParseError(RuntimeError):
    """IPA 解析失败（文件损坏 / 非法结构 / 缺关键字段）。"""


def _is_info_plist(name: str) -> bool:
    import re

    return bool(re.match(_INFO_PLIST_RE, name, re.IGNORECASE))


def parse_ipa_meta(path: str | Path) -> IpaMeta:
    """读取 IPA 的 bundle id / 版本号 / 构建号。

    抛 IpaParseError 时给出可执行的中文提示，不把底层异常直接抛给用户。
    """
    p = Path(path)
    if not p.exists():
        raise IpaParseError(f"IPA 文件不存在: {p}")
    if not zipfile.is_zipfile(p):
        raise IpaParseError(f"不是合法的 IPA/zip 文件: {p.name}")

    try:
        with zipfile.ZipFile(p) as zf:
            candidates = [n for n in zf.namelist() if _is_info_plist(n)]
            if not candidates:
                raise IpaParseError(
                    f"{p.name} 里找不到 Info.plist，结构不像 IPA"
                    "（应存在 Payload/<App>.app/Info.plist）"
                )

            # 正常情况下只有 1 个；多个时优先 CFBundlePackageType == APPL 的主 App
            chosen: dict | None = None
            for name in sorted(candidates, key=len):
                try:
                    plist = plistlib.loads(zf.read(name))
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(plist, dict):
                    continue
                if plist.get("CFBundlePackageType") == "APPL":
                    chosen = plist
                    break
                if chosen is None:
                    chosen = plist
            if chosen is None:
                raise IpaParseError(f"{p.name} 里的 Info.plist 无法解析")
    except zipfile.BadZipFile as exc:
        raise IpaParseError(f"{p.name} 不是有效的 zip: {exc}") from exc

    bundle_id = chosen.get("CFBundleIdentifier")
    version_name = chosen.get("CFBundleShortVersionString")
    build_number = chosen.get("CFBundleVersion")

    missing = [
        k
        for k, v in (
            ("CFBundleIdentifier", bundle_id),
            ("CFBundleShortVersionString", version_name),
            ("CFBundleVersion", build_number),
        )
        if not v
    ]
    if missing:
        raise IpaParseError(f"{p.name} 的 Info.plist 缺少字段: {', '.join(missing)}")

    return IpaMeta(
        bundle_id=str(bundle_id),
        version_name=str(version_name),
        build_number=str(build_number),
        app_name=chosen.get("CFBundleDisplayName") or chosen.get("CFBundleName"),
        minimum_os=chosen.get("MinimumOSVersion"),
    )
