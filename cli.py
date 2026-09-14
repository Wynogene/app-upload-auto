from __future__ import annotations

import json
import time

import click
from loguru import logger

from app.config import apply_proxy_env, get_settings
from app.core.rollout import RolloutSpecError, parse_rollout_percent
from app.core.service import AppReleaseService
from app.core.watch_targets import (
    CONSOLE_HINT,
    upsert_target,
    watch_hint_command,
)
from app.logging_setup import setup_logging
from app.models import Platform, StatusRequest, SubmitRequest, UploadRequest
from app.notify.feishu_notify import Notifier
from app.stores.release_notes import (
    _LOCALE_PREFIX_RE,
    _looks_like_locale,
    _normalize_locale,
)


def _platform(value: str) -> Platform:
    value = value.lower()
    if value == "ios":
        return Platform.IOS
    if value == "android":
        return Platform.ANDROID
    raise click.BadParameter("platform 仅支持 ios / android")


def _rollout_callback(ctx, param, value):
    """把 `--rollout 10` 这种百分比解析成 0~1 的小数比例。"""
    try:
        return parse_rollout_percent(value)
    except RolloutSpecError as exc:
        raise click.BadParameter(str(exc)) from exc


def _split_notes(values: tuple[str, ...]) -> tuple[str | None, dict[str, str] | None]:
    """把可重复的 --whats-new 拆成 (纯文本, 分语言字典)。

    `--whats-new "修复卡顿"`                  → 纯文本
    `--whats-new zh-CN=修复卡顿`             → 分语言
    `--whats-new zh-CN=... --whats-new en-US=...` → 多语言
    """
    plain: str | None = None
    scoped: dict[str, str] = {}

    for raw in values or ():
        if not raw or not str(raw).strip():
            continue
        text = str(raw).strip()
        m = _LOCALE_PREFIX_RE.match(text)
        if m and _looks_like_locale(m.group(1)):
            scoped[_normalize_locale(m.group(1))] = m.group(2).strip()
        else:
            plain = text
    return plain, (scoped or None)


def _echo_watch_hint_from_results(results: list) -> None:
    for r in results:
        msg = getattr(r, "message", "") or ""
        if "建议立刻执行" in msg:
            click.echo(msg, err=True)
            return
        details = getattr(r, "details", None) or {}
        track = str(details.get("track") or "").lower()
        vc = details.get("version_code")
        if getattr(r, "ok", False) and track in {"production", "prod"} and vc:
            hint = watch_hint_command(getattr(r, "app_id", ""), str(vc))
            click.echo(f"建议立刻盯盘:\n  {hint}\n（{CONSOLE_HINT}）", err=True)
            return


@click.group()
def cli() -> None:
    """APP 商店上传 / 提审 / 状态查询 CLI（默认个人调试安全模式）"""
    setup_logging()
    apply_proxy_env()
    settings = get_settings()
    if settings.safety_personal_only:
        click.echo(
            "[safety] PERSONAL_ONLY：飞书仅私聊本人；不改现网 webhook；默认无回调按钮",
            err=True,
        )


@cli.command("upload")
@click.option("--app-id", required=True)
@click.option("--platform", required=True, type=click.Choice(["ios", "android"]))
@click.option("--artifact", default=None, help="本地 IPA/AAB 路径")
@click.option("--artifact-url", default=None)
@click.option("--track", default=None, help="Android 轨道: internal|alpha|beta|production")
@click.option(
    "--whats-new",
    "whats_new",
    multiple=True,
    help="版本说明。可纯文本，或 `zh-CN=文本`；多次传入即多语言。正式版必填。",
)
@click.option(
    "--allow-production",
    is_flag=True,
    default=False,
    help="允许发到正式版轨道（有线上风险，需显式确认）",
)
@click.option(
    "--rollout",
    default=None,
    callback=_rollout_callback,
    help="分阶段发布百分比（仅 production）。如 --rollout 10 表示先放量 10%；100 表示全量。",
)
@click.option("--notify/--no-notify", default=True)
def upload_cmd(
    app_id: str,
    platform: str,
    artifact: str | None,
    artifact_url: str | None,
    track: str | None,
    whats_new: tuple[str, ...],
    allow_production: bool,
    rollout: float | None,
    notify: bool,
) -> None:
    """上传并发布到指定轨道（Android 默认 internal）。"""
    if (track or "").lower() in {"production", "prod"} and not allow_production:
        raise click.ClickException("正式版请同时加 --allow-production")
    plain, scoped = _split_notes(whats_new)
    service = AppReleaseService()
    req = UploadRequest(
        app_id=app_id,
        platform=_platform(platform),
        artifact_path=artifact,
        artifact_url=artifact_url,
        track=track,
        whats_new=plain,
        release_notes=scoped,
        allow_production=allow_production,
        rollout_fraction=rollout,
    )
    result = service.upload(req)
    click.echo(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    _echo_watch_hint_from_results([result])
    if notify:
        try:
            Notifier().notify_operation_results(app_id, [result])
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify failed: {}", exc)


@cli.command("release")
@click.option("--app-id", required=True)
@click.option("--platform", required=True, type=click.Choice(["ios", "android"]))
@click.option("--version-code", default=None, help="Android 已上传的 versionCode，如 1940")
@click.option("--track", required=True, help="目标轨道: internal|alpha|beta|production")
@click.option(
    "--whats-new",
    "whats_new",
    multiple=True,
    help="版本说明。可纯文本，或 `zh-CN=文本`；多次传入即多语言。正式版必填。",
)
@click.option(
    "--release-status",
    default="completed",
    type=click.Choice(["completed", "draft", "halted"]),
    help="completed=发布/送审；draft=草稿不对外",
)
@click.option("--allow-production", is_flag=True, default=False)
@click.option(
    "--rollout",
    default=None,
    callback=_rollout_callback,
    help="分阶段发布百分比（仅 production）。如 --rollout 20 表示续推到 20%；100 表示转全量。",
)
@click.option("--notify/--no-notify", default=True)
def release_cmd(
    app_id: str,
    platform: str,
    version_code: str | None,
    track: str,
    whats_new: tuple[str, ...],
    release_status: str,
    allow_production: bool,
    rollout: float | None,
    notify: bool,
) -> None:
    """将已上传版本推进到指定轨道（Android 发布/提审）。"""
    if track.lower() in {"production", "prod"} and not allow_production:
        raise click.ClickException("正式版请同时加 --allow-production")
    if _platform(platform) == Platform.ANDROID and not version_code:
        raise click.ClickException("Android release 需要 --version-code")
    if rollout is not None and release_status != "completed":
        raise click.ClickException(
            "`--rollout` 与 `--release-status` 不能同时指定"
            f"（当前 release-status={release_status}）。分批发布请只用 --rollout。"
        )
    plain, scoped = _split_notes(whats_new)
    service = AppReleaseService()
    req = SubmitRequest(
        app_id=app_id,
        platform=_platform(platform),
        version_code=version_code,
        track=track,
        whats_new=plain,
        release_notes=scoped,
        release_status=release_status,
        allow_production=allow_production,
        rollout_fraction=rollout,
    )
    result = service.submit(req)
    click.echo(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    _echo_watch_hint_from_results([result])
    if notify:
        try:
            Notifier().notify_operation_results(app_id, [result])
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify failed: {}", exc)


@cli.command("upload-submit")
@click.option("--app-id", required=True, help="config/apps.yaml 中的 id")
@click.option("--platform", required=True, type=click.Choice(["ios", "android"]))
@click.option("--artifact", default=None, help="本地 IPA/AAB 路径")
@click.option("--artifact-url", default=None, help="可下载的构建地址")
@click.option("--track", default=None, help="Android 轨道，默认用 apps.yaml")
@click.option(
    "--whats-new",
    "whats_new",
    multiple=True,
    help="版本说明。可纯文本，或 `zh-CN=文本`；多次传入即多语言。正式版必填。",
)
@click.option("--allow-production", is_flag=True, default=False)
@click.option(
    "--rollout",
    default=None,
    callback=_rollout_callback,
    help="分阶段发布百分比（仅 production）。如 --rollout 10 表示先放量 10%；100 表示全量。",
)
@click.option("--notify/--no-notify", default=True, help="是否推送飞书")
def upload_submit(
    app_id: str,
    platform: str,
    artifact: str | None,
    artifact_url: str | None,
    track: str | None,
    whats_new: tuple[str, ...],
    allow_production: bool,
    rollout: float | None,
    notify: bool,
) -> None:
    """Android：上传并发布到轨道；iOS：上传后再 submit（当前仍为骨架）。"""
    if (track or "").lower() in {"production", "prod"} and not allow_production:
        raise click.ClickException("正式版请同时加 --allow-production")
    plain, scoped = _split_notes(whats_new)
    service = AppReleaseService()
    req = UploadRequest(
        app_id=app_id,
        platform=_platform(platform),
        artifact_path=artifact,
        artifact_url=artifact_url,
        track=track,
        whats_new=plain,
        release_notes=scoped,
        allow_production=allow_production,
        rollout_fraction=rollout,
    )
    results = service.upload_and_submit(req)
    click.echo(json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2))
    _echo_watch_hint_from_results(results)
    if notify:
        try:
            Notifier().notify_operation_results(app_id, results)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify failed: {}", exc)


@cli.command("status")
@click.option("--app-id", required=True)
@click.option("--platform", default=None, type=click.Choice(["ios", "android"]))
@click.option("--version-code", default=None, help="Android：标注该 versionCode 出现在哪些轨道")
@click.option("--version-name", default=None, help="iOS：指定版本号，如 5.1049.126")
@click.option("--notify/--no-notify", default=False)
def status(
    app_id: str,
    platform: str | None,
    version_code: str | None,
    version_name: str | None,
    notify: bool,
) -> None:
    service = AppReleaseService()
    req = StatusRequest(
        app_id=app_id,
        platform=_platform(platform) if platform else None,
        version_code=version_code,
        version_name=version_name,
    )
    statuses = service.status(req)
    click.echo(json.dumps([s.model_dump() for s in statuses], ensure_ascii=False, indent=2))
    for s in statuses:
        click.echo(f"[{s.state.value}] {s.platform.value}/{s.app_id}: {s.message}", err=True)
    if notify:
        Notifier().notify_review_statuses(
            statuses,
            title="CLI 状态查询",
            footer=CONSOLE_HINT if (version_code or version_name) else None,
        )


@cli.command("ipa-check")
@click.option("--app-id", required=True, help="config/apps.yaml 中的 id")
@click.option("--ipa", required=True, help="本地 IPA 路径")
@click.option("--json-out", "json_out", is_flag=True, default=False)
def ipa_check(app_id: str, ipa: str, json_out: bool) -> None:
    """上传前校验 IPA（解析 + 与 App Store Connect 现状比对，不写入任何数据）。"""
    from app.config import get_app_by_id
    from app.stores.apple import AppleStoreClient

    app_cfg = get_app_by_id(app_id)
    if not app_cfg:
        raise click.ClickException(f"apps.yaml 中找不到 app id: {app_id}")

    result = AppleStoreClient().preflight_ipa(app_cfg, ipa)
    meta = result.get("meta")
    if json_out:
        payload = {
            "ok": result.get("ok"),
            "message": result.get("message"),
            "latest_released_version": result.get("latest_released_version"),
            "ipa": meta.__dict__ if meta else None,
            "issues": [i.__dict__ for i in result.get("issues") or []],
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))

    if meta:
        click.echo(
            f"IPA: bundle={meta.bundle_id} version={meta.version_name} build={meta.build_number}"
        )
    click.echo(result.get("message") or "")
    if not result.get("ok"):
        raise click.ClickException("上传前校验未通过")
    click.echo("[OK] 可以上传")


@cli.command("watch")
@click.option("--app-id", required=True)
@click.option("--platform", default="android", type=click.Choice(["ios", "android"]))
@click.option("--version-code", default=None, help="盯指定 Android versionCode（正式版提审后推荐）")
@click.option("--interval", default=30, show_default=True, help="轮询间隔（分钟）")
@click.option(
    "--heartbeat-hours",
    default=12.0,
    show_default=True,
    help="每隔 N 小时发一次「仍在盯，请到 Play Console 核对」；0=关闭心跳",
)
@click.option("--once", is_flag=True, default=False, help="只查一次就退出")
@click.option("--notify/--no-notify", default=True)
def watch(
    app_id: str,
    platform: str,
    version_code: str | None,
    interval: int,
    heartbeat_hours: float,
    once: bool,
    notify: bool,
) -> None:
    """定时查询发布/审核状态；有变化或到心跳点时私聊通知。"""
    service = AppReleaseService()
    last_fp = ""
    last_heartbeat_at = 0.0
    started_at = time.time()

    if version_code:
        upsert_target(
            app_id=app_id,
            platform=platform,
            version_code=version_code,
            track="production",
            heartbeat_hours=heartbeat_hours,
            note="cli watch",
        )

    click.echo(
        f"watching {app_id}/{platform}"
        + (f" versionCode={version_code}" if version_code else "")
        + f" every {interval}m"
        + (f", heartbeat every {heartbeat_hours}h" if heartbeat_hours > 0 else "")
        + " ...",
        err=True,
    )
    click.echo(f"提示: {CONSOLE_HINT}", err=True)

    while True:
        statuses = service.status(
            StatusRequest(
                app_id=app_id,
                platform=_platform(platform),
                version_code=version_code,
            )
        )
        now = time.time()
        for s in statuses:
            click.echo(f"[{s.state.value}] {s.message}")
            fp = f"{s.state.value}|{s.message}"
            if fp != last_fp:
                if last_fp and notify:
                    try:
                        Notifier().notify_review_statuses(
                            [s],
                            title="审核/发布状态变化",
                            footer=CONSOLE_HINT,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("notify failed: {}", exc)
                last_fp = fp
                if version_code:
                    from app.core.watch_targets import update_target_fields

                    update_target_fields(
                        f"{app_id}:{platform}:{version_code}",
                        last_fingerprint=fp,
                    )

            if heartbeat_hours > 0 and notify:
                base = last_heartbeat_at or started_at
                if (now - base) >= heartbeat_hours * 3600:
                    try:
                        Notifier().notify_review_statuses(
                            [s],
                            title="审核盯盘心跳提醒",
                            footer=CONSOLE_HINT,
                        )
                        last_heartbeat_at = now
                        if version_code:
                            from app.core.watch_targets import update_target_fields

                            update_target_fields(
                                f"{app_id}:{platform}:{version_code}",
                                last_heartbeat_at=now,
                            )
                        click.echo("[heartbeat] notified", err=True)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("heartbeat notify failed: {}", exc)

        if once:
            break
        time.sleep(max(1, interval) * 60)


@cli.command("apps")
@click.option(
    "--status/--no-status",
    default=False,
    help="向 Play 拉取各 App 正式版轨道（需网络/代理）；默认只读本地配置",
)
@click.option(
    "--include-disabled/--no-include-disabled",
    default=False,
    help="是否包含 android.enabled=false 的条目（如安欣看）",
)
@click.option("--json-out", "json_out", is_flag=True, default=False, help="输出 JSON")
def apps_cmd(status: bool, include_disabled: bool, json_out: bool) -> None:
    """打印 Android App 对照摘要（账号 / SA / 包名 / 新包门槛）。"""
    from app.core.apps_summary import (
        build_app_rows,
        enrich_rows_with_status,
        format_apps_table,
    )

    rows = build_app_rows(include_disabled=include_disabled)
    if status:
        click.echo("fetching Play production status…", err=True)
        rows = enrich_rows_with_status(rows)
    if json_out:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        click.echo(format_apps_table(rows))
        click.echo("详见 docs/ANDROID_APPS.md")


@cli.command("apple-check")
@click.option("--app-id", default=None, help="检查该 App 的凭据（读 apps.yaml 的 ios.*，缺省用 .env 全局）")
@click.option("--json-out", "json_out", is_flag=True, default=False, help="输出原始 JSON")
def apple_check(app_id: str | None, json_out: bool) -> None:
    """验证 App Store Connect 密钥（签名 JWT + 调用 /v1/apps）。"""
    from app.config import get_app_by_id
    from app.stores.apple import AppleStoreClient

    app_cfg = None
    if app_id:
        app_cfg = get_app_by_id(app_id)
        if not app_cfg:
            raise click.ClickException(f"apps.yaml 中找不到 app id: {app_id}")

    info = AppleStoreClient().check_connectivity(app_cfg)
    if json_out or not info.get("ok"):
        click.echo(json.dumps(info, ensure_ascii=False, indent=2))
    if info.get("ok"):
        click.echo(
            f"[OK] ASC 连通正常：可见 {len(info.get('apps') or [])} 个 App"
            f"（cred={info.get('cred_source')}, sub={info.get('key_required_sub')}, "
            f"use_proxy={info.get('use_proxy')}）"
        )
        for a in info.get("apps") or []:
            click.echo(f"  {a['bundle_id']} | {a['name']} | asc_id={a['asc_app_id']}")
        if info.get("target_app_visible") is False:
            click.echo(f"[warn] {info.get('warning')}", err=True)
    else:
        raise click.ClickException(str(info.get("message") or "ASC 连通性检查失败"))


@cli.command("panel")
@click.option("--app-id", required=True, help="个人模式下私聊给你一张说明卡片（默认无回调按钮）")
def panel(app_id: str) -> None:
    note = (
        "个人调试模式：请用本机 CLI 触发操作。"
        if get_settings().safety_personal_only
        else "点击下方按钮触发上传提审或查询状态。"
    )
    result = Notifier().send_app_panel(app_id, note=note)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command("serve")
def serve() -> None:
    settings = get_settings()
    if settings.safety_personal_only and settings.feishu_webhook_enabled:
        click.echo(
            "[warn] PERSONAL_ONLY 下仍开启了 WEBHOOK：请确认没有把现网事件 URL 指到本机",
            err=True,
        )
    if not settings.schedule_enabled:
        click.echo(
            "[hint] SCHEDULE_ENABLED=false：serve 不会轮询审核状态。"
            "需要常驻盯盘请设 SCHEDULE_ENABLED=true，并在 apps.yaml 设 poll_review_status: true。"
            "详见 docs/SCHEDULE_WATCH.md",
            err=True,
        )
    from app.main import run

    run()


if __name__ == "__main__":
    cli()
