# 运营化准备（个人模式）：切正式群清单 + serve 存活巡检
#
# 硬约束（当前必须保持）：
# - SAFETY_PERSONAL_ONLY=true  → 飞书只私聊本人，禁止群
# - FEISHU_WEBHOOK_ENABLED=false
# - 不改现网飞书事件订阅 URL
# - 盯盘 / 健康检查只读商店；健康检查失败时最多私聊你 + 本机拉起 serve

## 1. 现在就能用的：serve 存活巡检（只影响你）

```powershell
cd f:\app-upload-auto

# 查一次 / 手动巡检（PowerShell；计划任务已改用 pythonw，不再弹窗）
powershell -ExecutionPolicy Bypass -File .\scripts\windows\check-serve-watch.ps1 -NotifyIfDown

# 安装每 15 分钟巡检（pythonw.exe，无控制台窗口）
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install-serve-healthcheck.ps1
```

任务名：`AppUploadAuto-ServeHealth`（由 **pythonw** 执行，不应再闪前台 PowerShell）。  
卸载：`powershell -File .\scripts\windows\uninstall-serve-healthcheck.ps1`  
或随开机自启一起卸：`uninstall-autostart.ps1`。

`stop-serve-watch.ps1` 会禁用该巡检，避免停进程后又被拉起；`start-serve-watch.ps1` 会重新启用。

与登录自启 `AppUploadAuto-ServeWatch` 互补：自启管「登录后拉起」，健康检查管「中途挂了再拉」。

## 2. 切正式飞书群 — 清单（默认不要执行）

仅当你**明确**要运营化、且接受消息进群时再做。**现在不要改 `.env`。**

| 步骤 | 内容 | 风险 |
|------|------|------|
| A | 与业务确认群 `chat_id`、机器人已入群 | 误配会进错群 |
| B | `SAFETY_PERSONAL_ONLY=false` | 允许发群；失去「只给你」保护 |
| C | `apps.yaml` 填 `notify_chat_id` 或环境变量默认群 | 全组成员可见 |
| D | 评估是否开 `FEISHU_WEBHOOK_ENABLED` / 卡片回调 | 可能影响现网订阅，需单独评审 |
| E | 先小流量：只开一个 app 的群通知试一天 | — |
| F | 保留个人兜底：出问题立刻改回 `SAFETY_PERSONAL_ONLY=true` | — |

**在完成 iOS 真上传验证、发版手册跑顺之前，保持个人模式。**

## 3. 本机端口

健康检查默认：`http://127.0.0.1:18088/health`（与 `start-serve-watch.ps1` 一致）。
