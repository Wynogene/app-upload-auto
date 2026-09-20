from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT_DIR / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Safety (default: personal-only, zero prod blast radius) ---
    # true: 只允许发给 OWNER + 正式通知 user_id 名单；禁止群聊；默认不挂现网 webhook
    safety_personal_only: bool = True
    # true: 允许启动 /feishu/webhook（仍建议不要改现网事件订阅地址）
    feishu_webhook_enabled: bool = False
    # personal-only 下默认不发带 callback 的按钮，避免点到现网 webhook
    feishu_enable_card_callbacks: bool = False

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    # 兼容旧字段；personal-only 模式下会被忽略（除非走 resolve 逻辑）
    feishu_default_chat_id: str = ""
    # 调试消息只发给本人（企业 user_id，如 56798dag）
    feishu_owner_user_id: str = ""
    # 正式通知名单（逗号分隔 user_id）：盯盘 / 传包 / 提审 / 审核状态等
    feishu_notify_user_ids: str = ""
    # 仅用于飞书卡片回调校验操作者；发消息一律走 user_id
    feishu_owner_open_id: str = ""
    feishu_receive_id_type: Literal["open_id", "user_id", "chat_id"] = "user_id"
    # 飞书 open.feishu.cn 是否走代理。默认 false（直连）；系统 HTTP_PROXY 常指向本机
    # 7892，代理挂了会导致盯盘已检测到变化但私聊通知失败。
    feishu_use_proxy: bool = False

    apple_key_id: str = ""
    apple_issuer_id: str = ""
    apple_private_key_path: str = "secrets/AuthKey_XXXXX.p8"
    # JWT 的 sub 声明。留空 = 按 .p8 文件名自动判断：
    #   ApiKey_*.p8  -> 个人 API 密钥（Individual Key），必须带 sub="user"
    #   AuthKey_*.p8 -> 团队 API 密钥（Team Key），不能带 sub
    # 显式填写则强制使用该值（填 "none"/"-" 表示不带 sub）。
    apple_token_sub: str = ""
    # ASC 是否走代理。默认 false（直连 api.appstoreconnect.apple.com）
    apple_use_proxy: bool = False

    google_play_service_account_json: str = "secrets/google-play-sa-blurams-easelife.json"
    # 版本说明默认值。按 App 可在 apps.yaml 的 android.* 覆盖；
    # iOS 提审未在 ios.* 单独配置时回退到 android / 本全局，与 Play 保持一致。
    # 语言标签须与商店支持的本地化语言一致；逗号分隔。
    release_notes_locales: str = "en-US"
    # 运营已确认的默认文案（不是工具编造的）。留空则正式版 / iOS 提审必须显式传 --whats-new。
    release_notes_default: str = ""
    # 正式版未传 --rollout 时的默认分批百分比。留空字符串 = 关闭默认分批（不传即全量）。
    # 可被 apps.yaml 的 android.rollout_percent_default 覆盖。
    rollout_percent_default: str = "5"
    # 访问 Google API 的本地代理（httplib2 不会自动读系统代理）
    http_proxy: str = ""
    https_proxy: str = ""
    # ai_support 仓库根目录（可选；只读 import utils_synology，不改对方代码）
    ai_support_root: str = ""
    # 群晖 FileStation（本仓库自实现下载分享包；推荐配置，避免依赖对方 boto3）
    synology_base_url: str = "http://delivery.vaas.plus:5000"
    synology_username: str = ""
    synology_password: str = ""

    host: str = "127.0.0.1"  # 本机调试默认不对外
    port: int = 8088
    status_poll_interval_minutes: int = 30
    # personal-only 默认关闭定时，避免无意刷屏；确认后再开
    schedule_enabled: bool = False

    apps_config_path: str = Field(default=str(CONFIG_DIR / "apps.yaml"))
    log_dir: str = Field(default=str(ROOT_DIR / "logs"))


def apply_proxy_env() -> None:
    """Ensure process env has proxy for libs that read HTTP(S)_PROXY."""
    import os

    settings = get_settings()
    http_proxy = settings.http_proxy or settings.https_proxy
    https_proxy = settings.https_proxy or settings.http_proxy
    if http_proxy:
        os.environ.setdefault("HTTP_PROXY", http_proxy)
        os.environ.setdefault("http_proxy", http_proxy)
    if https_proxy:
        os.environ.setdefault("HTTPS_PROXY", https_proxy)
        os.environ.setdefault("https_proxy", https_proxy)


def get_google_proxy_url() -> str:
    settings = get_settings()
    return (settings.https_proxy or settings.http_proxy or "").strip()


def resolve_apple_token_sub(key_path: str | Path | None = None, explicit: str | None = None) -> str | None:
    """Return the JWT `sub` claim Apple expects, or None if it must be omitted.

    Apple 有两类 App Store Connect API 密钥，JWT 要求不同：
      * 团队密钥 (Team Key)，下载文件名 AuthKey_<KEYID>.p8 → 不要 sub
      * 个人密钥 (Individual Key)，下载文件名 ApiKey_<KEYID>.p8 → sub 必须为 "user"
    判定错误会直接导致 401 NOT_AUTHORIZED。

    `explicit` 用于按 App 覆盖（apps.yaml 的 ios.token_sub）；None 表示退回全局 .env。
    """
    settings = get_settings()
    raw = (settings.apple_token_sub if explicit is None else explicit) or ""
    raw = raw.strip()
    if raw:
        return None if raw.lower() in {"none", "-", "null"} else raw

    path = Path(key_path or settings.apple_private_key_path)
    if path.name.startswith("ApiKey_"):
        return "user"
    if path.name.startswith("AuthKey_"):
        return None
    # 拿不准时按个人密钥处理：这是目前最常用的形态
    return "user"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_apps_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path or get_settings().apps_config_path)
    if not cfg_path.exists():
        example = CONFIG_DIR / "apps.example.yaml"
        if example.exists():
            return yaml.safe_load(example.read_text(encoding="utf-8")) or {}
        return {"apps": [], "schedule": {}}
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def get_app_by_id(app_id: str) -> dict[str, Any] | None:
    for app in load_apps_config().get("apps") or []:
        if app.get("id") == app_id:
            return app
    return None


def resolve_owner_target() -> tuple[str, str]:
    """Resolve personal debug owner as (receive_id_type, receive_id). Always user_id."""
    settings = get_settings()
    if settings.feishu_owner_user_id:
        return "user_id", settings.feishu_owner_user_id.strip()
    raise RuntimeError(
        "请配置 FEISHU_OWNER_USER_ID（企业 user_id，调试消息只发给此人）"
    )


def parse_user_id_list(raw: str | None = None) -> list[str]:
    """Parse comma/semicolon-separated user_ids; de-dupe, keep order."""
    text = get_settings().feishu_notify_user_ids if raw is None else raw
    seen: set[str] = set()
    out: list[str] = []
    for part in (text or "").replace(";", ",").split(","):
        uid = part.strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def personal_allowed_user_ids() -> set[str]:
    """user_id allowlist under SAFETY_PERSONAL_ONLY（调试本人 + 正式名单）。"""
    settings = get_settings()
    ids: set[str] = set()
    if settings.feishu_owner_user_id:
        ids.add(settings.feishu_owner_user_id.strip())
    ids.update(parse_user_id_list())
    return {x for x in ids if x}


def resolve_notify_targets(
    app_id: str | None = None,
    *,
    audience: Literal["ops", "debug"] = "ops",
) -> list[tuple[str, str]]:
    """Return (receive_id_type, receive_id) list for a notify fan-out.

    - debug：始终只发 FEISHU_OWNER_USER_ID（健康检查 / 个人调试卡），绝不进群
    - ops + personal-only：FEISHU_NOTIFY_USER_IDS（未配则回退本人）
    - ops + 非 personal-only：apps.yaml notify_chat_id / 默认群
    """
    # 调试通道与 SAFETY_PERSONAL_ONLY 无关：重启健康检查等绝不能进业务群
    if audience == "debug":
        return [resolve_owner_target()]

    settings = get_settings()
    if settings.safety_personal_only:
        uids = parse_user_id_list()
        if not uids:
            return [resolve_owner_target()]
        return [("user_id", uid) for uid in uids]

    if app_id:
        app = get_app_by_id(app_id)
        if app and app.get("notify_chat_id"):
            return [("chat_id", app["notify_chat_id"])]
    if settings.feishu_default_chat_id:
        return [(settings.feishu_receive_id_type, settings.feishu_default_chat_id)]
    return [resolve_owner_target()]


def resolve_notify_target(
    app_id: str | None = None,
    *,
    audience: Literal["ops", "debug"] = "ops",
) -> tuple[str, str]:
    """Return the primary (receive_id_type, receive_id)."""
    return resolve_notify_targets(app_id, audience=audience)[0]
