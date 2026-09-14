"""Extract packageName / versionCode / versionName from an Android App Bundle (.aab)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile


@dataclass(frozen=True)
class AabMeta:
    package_name: str
    version_code: str
    version_name: str | None = None


def _proto_len_string_after(data: bytes, key: bytes) -> str | None:
    """Read length-delimited UTF-8 string immediately after `key` in aapt protobuf XML."""
    idx = 0
    while True:
        i = data.find(key, idx)
        if i < 0:
            return None
        j = i + len(key)
        # Attribute name is followed by value as length-delimited field (tag 0x1a)
        if j < len(data) and data[j] == 0x1A:
            length = data[j + 1]
            raw = data[j + 2 : j + 2 + length]
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
        idx = i + 1


def parse_aab_meta(path: str | Path) -> AabMeta:
    aab = Path(path)
    if not aab.exists():
        raise FileNotFoundError(f"AAB 不存在: {aab}")
    try:
        with ZipFile(aab) as zf:
            try:
                data = zf.read("base/manifest/AndroidManifest.xml")
            except KeyError as exc:
                raise ValueError("AAB 内缺少 base/manifest/AndroidManifest.xml") from exc
    except BadZipFile as exc:
        raise ValueError(f"不是有效的 AAB/ZIP: {aab}") from exc

    package = _proto_len_string_after(data, b"package")
    version_code = _proto_len_string_after(data, b"versionCode")
    version_name = _proto_len_string_after(data, b"versionName")
    if not package or not version_code:
        raise ValueError(
            f"无法从 AAB 解析 package/versionCode（package={package!r}, versionCode={version_code!r}）"
        )
    if not version_code.isdigit():
        raise ValueError(f"AAB versionCode 非法: {version_code!r}")
    return AabMeta(
        package_name=package,
        version_code=version_code,
        version_name=version_name,
    )
