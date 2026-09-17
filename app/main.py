from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from loguru import logger

from app.config import get_settings
from app.feishu.actions import handle_card_action
from app.logging_setup import setup_logging
from app.schedule.jobs import shutdown_scheduler, start_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    settings = get_settings()
    if settings.schedule_enabled and not settings.safety_personal_only:
        start_scheduler()
    elif settings.schedule_enabled and settings.safety_personal_only:
        # 个人模式允许定时，但通知目标已被强制为本人
        start_scheduler()
        logger.info("personal-only schedule enabled; notifications go only to owner open_id")
    else:
        logger.info("scheduler disabled (SCHEDULE_ENABLED=false)")
    yield
    shutdown_scheduler()


app = FastAPI(title="App Upload Auto", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    settings = get_settings()
    return {
        "ok": True,
        "safety_personal_only": settings.safety_personal_only,
        "webhook_enabled": settings.feishu_webhook_enabled,
    }


@app.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """Feishu event callback.

    安全约定（个人调试）：
    - 默认 FEISHU_WEBHOOK_ENABLED=false，避免被误挂到现网事件地址
    - 不要修改现网应用的事件订阅 URL
    """
    settings = get_settings()
    params = await request.json()
    logger.info("feishu webhook <= keys={}", list(params.keys()))

    if params.get("type") == "url_verification":
        if not settings.feishu_webhook_enabled:
            raise HTTPException(status_code=403, detail="webhook disabled")
        token = params.get("token")
        if settings.feishu_verification_token and token != settings.feishu_verification_token:
            return {"error": "invalid verification token"}
        return {"challenge": params.get("challenge")}

    if not settings.feishu_webhook_enabled:
        raise HTTPException(status_code=403, detail="webhook disabled (FEISHU_WEBHOOK_ENABLED=false)")

    # personal-only：只处理本人点击，忽略其他人
    header = params.get("header") or {}
    event_type = header.get("event_type")
    if event_type == "card.action.trigger":
        event = params.get("event") or {}
        operator = (event.get("operator") or {}).get("open_id")
        if settings.safety_personal_only:
            # 卡片回调里通常带 open_id；若只配了 user_id，则无法在此精确比对，直接拒绝陌生人 open_id
            owner_open = settings.feishu_owner_open_id
            if owner_open:
                allowed = operator == owner_open
            else:
                # 仅配置了 user_id 时：个人调试默认不开放 webhook，走到这里也应拒绝非空校验失败场景
                allowed = False
            if not allowed:
                return {
                    "toast": {
                        "type": "info",
                        "content": "个人调试模式：仅所有者可操作",
                        "i18n": {"zh_cn": "个人调试模式：仅所有者可操作"},
                    }
                }
        action = event.get("action") or {}
        value = action.get("value") or {}
        if action.get("tag") == "button":
            return handle_card_action(value, operator_open_id=operator)
        return {
            "toast": {
                "type": "info",
                "content": "暂不支持该交互",
                "i18n": {"zh_cn": "暂不支持该交互"},
            }
        }

    return {}


def run() -> None:
    import uvicorn

    settings = get_settings()
    setup_logging()
    if settings.safety_personal_only:
        logger.warning(
            "以个人调试模式启动：正式通知→FEISHU_NOTIFY_USER_IDS，调试→FEISHU_OWNER_USER_ID；"
            "请勿修改现网飞书事件订阅 URL"
        )
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
