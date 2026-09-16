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
    # true: 只允许发给 FEISHU_OWNER_OPEN_ID；禁止群聊；默认不挂现网 webhook
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
    # 本人标识二选一即可（优先 open_id）
    # open_id 形如 ou_...；你们口头说的「飞书 uid」多半是 user_id（如 50000000）
    feishu_owner_open_id: str = ""
    feishu_owner_user_id: str = ""
    feishu_receive_id_type: Literal["open_id", "user_id", "chat_id"] = "open_id"
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
    # 版本说明（releaseNotes）默认值。按 App 可在 apps.yaml 的 android.* 覆盖。
    # 语言标签须与 Play 商店支持的本地化语言一致；逗号分隔。
    release_notes_locales: str = "en-US"
    # 运营已确认的默认文案（不是工具编造的）。留空则正式版必须显式传 --whats-new。
    release_notes_default: str = ""
    # 正式版未传 --rollout 时的默认分批百分比。留空字符串 = 关闭默认分批（不传即全量）。
    # 可被 apps.yaml 的 android.rollout_percent_default 覆盖。
    rollout_percent_default: str = "5"
    # 访问 Google API 的本地代理（httplib2 不会自动读系统代理）
    http_proxy: str = ""
    https_proxy: str = ""

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
    """Resolve personal owner as (receive_id_type, receive_id)."""
    settings = get_settings()
    if settings.feishu_owner_open_id:
        return "open_id", settings.feishu_owner_open_id
    if settings.feishu_owner_user_id:
        return "user_id", settings.feishu_owner_user_id
    raise RuntimeError(
        "请配置 FEISHU_OWNER_OPEN_ID（ou_...）或 FEISHU_OWNER_USER_ID（企业 user_id，如 50000000）"
    )


def resolve_notify_target(app_id: str | None = None) -> tuple[str, str]:
    """Return (receive_id_type, receive_id) with personal-only hard guard."""
    settings = get_settings()
    if settings.safety_personal_only:
        # 强制私聊本人，忽略任何群 chat_id / apps.yaml notify_chat_id
        return resolve_owner_target()

    if app_id:
        app = get_app_by_id(app_id)
        if app and app.get("notify_chat_id"):
            return "chat_id", app["notify_chat_id"]
    if settings.feishu_default_chat_id:
        return settings.feishu_receive_id_type, settings.feishu_default_chat_id
    return resolve_owner_target()
