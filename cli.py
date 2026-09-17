from __future__ import annotations

import json
import time

import click
from loguru import logger

from app.config import apply_proxy_env, get_settings
from app.core.rollout import RolloutSpecError, parse_rollout_percent
from app.core.service import AppReleaseService
from app.core.watch_targets import (
    ANDROID_HINT,
    console_hint_for,
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
            click.echo(f"建议立刻盯盘:\n  {hint}\n（{ANDROID_HINT}）", err=True)
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
    help="分阶段发布百分比（仅 production）。省略则用配置默认 5%；--rollout 100 表示全量。",
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
    help="分阶段发布百分比（仅 production）。省略则用配置默认 5%；续推如 --rollout 20；100 表示转全量。",
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
    help="分阶段发布百分比（仅 production）。省略则用配置默认 5%；--rollout 100 表示全量。",
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
        plats = [s.platform.value for s in statuses]
        from app.core.watch_targets import console_hints_for

        Notifier().notify_review_statuses(
            statuses,
            title="CLI 状态查询",
            footer=console_hints_for(plats) if (version_code or version_name) else None,
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

    if version_code or platform == "ios":
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
    click.echo(f"提示: {console_hint_for(platform)}", err=True)

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
                    prev_state, prev_msg = (
                        last_fp.split("|", 1) if "|" in last_fp else (last_fp, "")
                    )
                    from app.core.notify_titles import review_change_notify_title

                    title = review_change_notify_title(
                        prev_state,
                        s.state,
                        previous_message=prev_msg,
                        current_message=s.message,
                    )
                    try:
                        Notifier().notify_review_statuses(
                            [s],
                            title=title,
                            footer=console_hint_for(s.platform.value),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("notify failed: {}", exc)
                last_fp = fp
                from app.core.watch_targets import update_target_fields

                key_vc = version_code or "-"
                update_target_fields(
                    f"{app_id}:{platform}:{key_vc}",
                    last_fingerprint=fp,
                )

            if heartbeat_hours > 0 and notify:
                base = last_heartbeat_at or started_at
                if (now - base) >= heartbeat_hours * 3600:
                    try:
                        Notifier().notify_review_statuses(
                            [s],
                            title="审核盯盘心跳提醒",
                            footer=console_hint_for(s.platform.value),
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


@cli.command("apps-ready")
@click.option("--app-id", "app_ids", multiple=True, help="只检查指定 app（可重复）；默认全部启用项")
@click.option(
    "--include-disabled/--no-include-disabled",
    default=False,
    help="是否包含双端都 disabled 的条目",
)
@click.option(
    "--probe-asc/--no-probe-asc",
    default=True,
    help="是否只读探测 ASC（确认 App 对密钥可见）；默认开",
)
@click.option("--json-out", "json_out", is_flag=True, default=False, help="输出 JSON")
def apps_ready_cmd(
    app_ids: tuple[str, ...],
    include_disabled: bool,
    probe_asc: bool,
    json_out: bool,
) -> None:
    """真上传前多 App 就绪检查（只读：配置/密钥/ASC 可见性/盯盘覆盖，不写商店）。"""
    from app.core.apps_ready import format_apps_ready, items_to_dicts, run_apps_ready

    items = run_apps_ready(
        include_disabled=include_disabled,
        probe_asc=probe_asc,
        app_ids=list(app_ids) or None,
    )
    if json_out:
        click.echo(json.dumps(items_to_dicts(items), ensure_ascii=False, indent=2))
    else:
        click.echo(format_apps_ready(items))
    if any(not i.ok for i in items):
        raise SystemExit(1)


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


@cli.command("ios-whats-new")
@click.option("--app-id", required=True, help="apps.yaml 中的 App id，如 blurams")
@click.option(
    "--version",
    "version_name",
    default=None,
    help="营销版本号，如 5.1049.126；省略则选最新可编辑版本",
)
@click.option(
    "--whats-new",
    "whats_new",
    multiple=True,
    help="版本说明。可纯文本或 `en-US=文本`；省略则用与 Android 相同的默认文案",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    default=False,
    help="真正写入 ASC。默认只 dry-run 预览，不改商店。",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="连已有 what's New 的语言也覆盖；默认只填空的",
)
@click.option("--json-out", "json_out", is_flag=True, default=False)
def ios_whats_new_cmd(
    app_id: str,
    version_name: str | None,
    whats_new: tuple[str, ...],
    do_apply: bool,
    force: bool,
    json_out: bool,
) -> None:
    """提审前：仅为「已存在的」本地化补全 what's New（默认 dry-run）。

    不会新建未本地化的语言；不改构建/发布方式/分批；不自动点提审。
    """
    from app.config import get_app_by_id
    from app.stores.apple import AppleStoreClient
    from app.stores.apple_whats_new import (
        LocalizationRow,
        plan_whats_new_updates,
        summarize_plan,
    )
    from app.stores.release_notes import build_release_notes

    app_cfg = get_app_by_id(app_id)
    if not app_cfg:
        raise click.ClickException(f"apps.yaml 中找不到 app id: {app_id}")

    plain, scoped = _split_notes(whats_new)
    raw: list[str] = []
    if scoped:
        for lo, txt in scoped.items():
            raw.append(f"{lo}={txt}")
    if plain:
        raw.append(plain)

    notes, notes_source = build_release_notes(
        raw or None,
        app_cfg,
        platform="ios",
    )
    if not notes:
        raise click.ClickException(
            "没有可用的版本说明：请传 --whats-new，或配置 "
            "android.release_notes_default / RELEASE_NOTES_DEFAULT"
        )

    text_by_locale = {n["language"]: n["text"] for n in notes}
    fallback_text = next(iter(text_by_locale.values()))

    client = AppleStoreClient()
    ver = client.resolve_version_for_whats_new(app_cfg, version_name=version_name)
    if not ver.get("ok"):
        raise click.ClickException(str(ver.get("message") or "无法解析版本"))

    version = ver["version"]
    loc = client.list_version_localizations(app_cfg, version["id"])
    if not loc.get("ok"):
        raise click.ClickException(str(loc.get("message") or "无法列出本地化"))

    rows = [
        LocalizationRow(
            id=str(x["id"]),
            locale=str(x["locale"]),
            whats_new=str(x.get("whatsNew") or ""),
        )
        for x in loc.get("localizations") or []
    ]
    plan = plan_whats_new_updates(
        rows,
        text_by_locale=text_by_locale,
        fallback_text=fallback_text,
        fill_empty_only=not force,
    )
    summary = summarize_plan(plan)
    to_fill = [p for p in plan if p.action == "fill"]

    report = {
        "mode": "apply" if do_apply else "dry-run",
        "app_id": app_id,
        "version_string": version.get("version_string"),
        "version_id": version.get("id"),
        "version_state": version.get("state"),
        "version_editable": bool(version.get("editable")),
        "notes_source": notes_source,
        "fallback_text": fallback_text,
        "existing_locales": [r.locale for r in rows],
        "summary": summary,
        "plan": [
            {
                "locale": p.locale,
                "action": p.action,
                "current": (p.current[:60] + "…") if len(p.current) > 60 else p.current,
                "planned": (p.planned[:60] + "…") if len(p.planned) > 60 else p.planned,
            }
            for p in plan
        ],
    }

    if json_out:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        click.echo(
            f"[{report['mode']}] {app_id} iOS {version.get('version_string')} "
            f"state={version.get('state')} editable={version.get('editable')}"
        )
        click.echo(
            f"已存在本地化 {len(rows)} 种（不会新建语言）："
            + (", ".join(r.locale for r in rows) or "(无)")
        )
        click.echo(
            f"文案来源={notes_source}；将填空={summary.get('fill', 0)}；"
            f"已有跳过={summary.get('skip_has_text', 0)}；"
            f"相同跳过={summary.get('skip_same', 0)}"
        )
        for p in plan:
            mark = {
                "fill": "WRITE",
                "skip_has_text": "skip ",
                "skip_same": "same ",
                "skip_no_text": "EMPTY",
            }.get(p.action, p.action)
            click.echo(f"  [{mark}] {p.locale}: {(p.current or '(空)')[:40]!r} -> {p.planned[:40]!r}")

    if not do_apply:
        click.echo(
            "\n未写入商店（dry-run）。确认无误后加 --apply 才会 PATCH what's New。",
            err=True,
        )
        return

    if not version.get("editable"):
        raise click.ClickException(
            f"版本状态 `{version.get('state')}` 通常不可改 what's New，已拒绝写入。"
            "请换可编辑版本，或到 ASC 确认。"
        )
    if not to_fill:
        click.echo("没有需要写入的语言，已退出。", err=True)
        return

    patches = [
        {"id": p.localization_id, "locale": p.locale, "whatsNew": p.planned}
        for p in to_fill
    ]
    applied = client.apply_whats_new_patches(app_cfg, patches)
    if json_out:
        click.echo(json.dumps(applied, ensure_ascii=False, indent=2))
    else:
        click.echo(
            f"写入完成：ok={applied.get('ok')} patched={applied.get('patched')} "
            f"failed={applied.get('failed')}"
        )
    if not applied.get("ok"):
        raise SystemExit(1)


@cli.command("submit-card")
@click.option("--app-id", required=True)
@click.option("--platform", required=True, type=click.Choice(["ios", "android"]))
@click.option("--artifact", "artifact_path", default=None, help="本地 AAB/IPA 或 zip 路径")
@click.option("--artifact-url", default=None, help="包下载链接（可为 zip）")
@click.option("--whats-new", default=None)
@click.option("--version", default=None, help="版本号（展示/校验提示）")
@click.option(
    "--track",
    default="internal",
    show_default=True,
    help="Android 轨道；调试默认 internal",
)
@click.option(
    "--allow-production",
    is_flag=True,
    default=False,
    help="仅当 track=production 时需要显式打开",
)
@click.option(
    "--with-callbacks",
    is_flag=True,
    default=False,
    help="附带可点按钮（需 FEISHU_ENABLE_CARD_CALLBACKS=true；勿改现网事件 URL）",
)
@click.option(
    "--preview-only",
    is_flag=True,
    default=False,
    help="按钮仅供预览：即使被点击也不会上传/写商店",
)
@click.option("--note", default="", help="卡片附加说明")
def submit_card(
    app_id: str,
    platform: str,
    artifact_path: str | None,
    artifact_url: str | None,
    whats_new: str | None,
    version: str | None,
    track: str,
    allow_production: bool,
    with_callbacks: bool,
    preview_only: bool,
    note: str,
) -> None:
    """私聊本人一张提审调试卡（不写商店）。默认无按钮；用 card-run 真正执行。"""
    settings = get_settings()
    if not settings.safety_personal_only:
        raise click.ClickException(
            "submit-card 仅允许 SAFETY_PERSONAL_ONLY=true，防止误发业务群"
        )
    if not artifact_path and not artifact_url:
        raise click.ClickException("请提供 --artifact 或 --artifact-url")
    if track in {"production", "prod"} and not allow_production:
        raise click.ClickException(
            "正式轨需同时加 --allow-production；调试请用默认 --track internal"
        )
    if with_callbacks:
        if not settings.feishu_enable_card_callbacks:
            raise click.ClickException(
                "--with-callbacks 需要本进程 FEISHU_ENABLE_CARD_CALLBACKS=true"
            )
        if not settings.feishu_webhook_enabled:
            click.echo(
                "[warn] FEISHU_WEBHOOK_ENABLED=false：即使有按钮，点击也不会打到本机。"
                "若要本地点按，请用独立调试应用 + 本机 serve，且不要改现网事件 URL。",
                err=True,
            )

    from app.feishu.actions import build_submit_card_value

    value = build_submit_card_value(
        app_id=app_id,
        platform=platform,
        artifact_path=artifact_path,
        artifact_url=artifact_url,
        whats_new=whats_new,
        version=version,
        track=track,
        allow_production=allow_production,
        preview_only=preview_only,
    )
    result = Notifier().send_submit_debug_card(
        value, note=note, with_callbacks=with_callbacks
    )
    click.echo(json.dumps({"value": value, "feishu": result}, ensure_ascii=False, indent=2))
    click.echo(
        "\n下一步（推荐，不依赖飞书按钮）:\n"
        f"  python cli.py card-run --app-id {app_id} --platform {platform}"
        + (f' --artifact "{artifact_path}"' if artifact_path else "")
        + (f' --artifact-url "{artifact_url}"' if artifact_url else "")
        + f" --track {track}"
        + (" --allow-production" if allow_production else "")
        + (f' --whats-new "{whats_new}"' if whats_new else ""),
        err=True,
    )


@cli.command("card-run")
@click.option("--app-id", required=True)
@click.option("--platform", required=True, type=click.Choice(["ios", "android"]))
@click.option("--artifact", "artifact_path", default=None)
@click.option("--artifact-url", default=None)
@click.option("--whats-new", default=None)
@click.option("--version", default=None)
@click.option("--track", default="internal", show_default=True)
@click.option("--allow-production", is_flag=True, default=False)
@click.option(
    "--dry-resolve",
    is_flag=True,
    default=False,
    help="只下载/解压选型，不写商店（最安全自检）",
)
def card_run(
    app_id: str,
    platform: str,
    artifact_path: str | None,
    artifact_url: str | None,
    whats_new: str | None,
    version: str | None,
    track: str,
    allow_production: bool,
    dry_resolve: bool,
) -> None:
    """按与调试卡相同的契约执行提审（默认同轨 internal）。结果私聊本人。"""
    if not artifact_path and not artifact_url:
        raise click.ClickException("请提供 --artifact 或 --artifact-url")
    if track in {"production", "prod"} and not allow_production:
        raise click.ClickException("正式轨需 --allow-production；调试请用 --track internal")

    from app.config import get_app_by_id
    from app.core.artifact_resolve import cleanup_work_dir, resolve_artifact
    from app.feishu.actions import build_submit_card_value, run_upload_submit_job

    if dry_resolve:
        app = get_app_by_id(app_id)
        if not app:
            raise click.ClickException(f"未知 app_id: {app_id}")
        plat = _platform(platform)
        hint = None
        if plat == Platform.ANDROID:
            hint = (app.get("android") or {}).get("package_name")
        else:
            hint = (app.get("ios") or {}).get("bundle_id")
        resolved = resolve_artifact(
            platform=plat,
            artifact_path=artifact_path,
            artifact_url=artifact_url,
            package_or_bundle=hint,
            version_hint=version,
        )
        click.echo(
            json.dumps(
                {
                    "ok": resolved.ok,
                    "message": resolved.message,
                    "path": str(resolved.path) if resolved.path else None,
                    "source": resolved.source,
                    "candidates": resolved.candidates,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        cleanup_work_dir(resolved.work_dir)
        if not resolved.ok:
            raise SystemExit(1)
        return

    value = build_submit_card_value(
        app_id=app_id,
        platform=platform,
        artifact_path=artifact_path,
        artifact_url=artifact_url,
        whats_new=whats_new,
        version=version,
        track=track,
        allow_production=allow_production,
    )
    click.echo(
        f"[card-run] 开始执行 track={track} allow_production={allow_production} …",
        err=True,
    )
    results = run_upload_submit_job(value, operator_open_id=get_settings().feishu_owner_open_id or None)
    click.echo(json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2))
    if not all(r.ok for r in results):
        raise SystemExit(1)


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
