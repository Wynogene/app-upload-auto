# App Upload Auto

独立的 iOS / Android 商店上传、提审与盯盘工具（Python + FastAPI CLI）。

默认 **`SAFETY_PERSONAL_ONLY=true`**：飞书只私聊本人，不改现网事件订阅，不往业务群发消息。  
盯盘 / 状态查询为**只读**；上传与提审会写商店，请先用测试轨或确认版本号。

## 当前进度（以代码为准）

| 能力 | Android (Google Play) | iOS (App Store Connect) |
|------|------------------------|-------------------------|
| 鉴权 / 连通自检 | ✅ 服务账号 JSON | ✅ `apple-check`（`.p8`） |
| 上传 | ✅ AAB（含正式版） | ⏳ Build Upload API（**Windows 可直传，不需 Mac**） |
| 提审 / 推进轨道 | ✅ `upload` / `upload-submit` / `release` | ⏳ `reviewSubmissions` 待接入 |
| 分阶段发布 | ✅ `--rollout`（正式版默认约 5%） | ✅ 只读盯盘（Apple 固定 7 天曲线） |
| 状态 / 过审生命周期 | ✅ `status` + lifecycle | ✅ `status`（含分批进度） |
| 上传前校验 | （版本码由 Play 拦截） | ✅ `ipa-check` |
| 盯盘通知 | ✅ `watch` / `serve` 调度 | ✅ 同上 |
| 运营向飞书文案 | ✅ 风格 D 字段表 | ✅ 版本精确到构建号 |

多 App：`blurams` + `easelife` 共用一套 Play SA / Apple 团队密钥；`boykeep` 为独立主体（Android SA 已配，iOS 需单独 `.p8`）。

## 个人调试（推荐）

先看：[docs/SAFE_RUNBOOK.md](docs/SAFE_RUNBOOK.md)。

发版固定命令：[docs/RELEASE_PLAYBOOK.md](docs/RELEASE_PLAYBOOK.md)  
多 App 就绪检查：`python cli.py apps-ready` · [docs/MULTI_APP_READY.md](docs/MULTI_APP_READY.md)  
常驻盯盘（方式 B）：[docs/SCHEDULE_WATCH.md](docs/SCHEDULE_WATCH.md)  
巡检 / 切群清单（默认勿切群）：[docs/OPS_PERSONAL_ONLY.md](docs/OPS_PERSONAL_ONLY.md)

## 本地启动（Windows）

```powershell
cd f:\app-upload-auto
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# 编辑 .env（商店密钥、飞书、代理）与 config\apps.yaml
# 常驻盯盘示例：
#   SCHEDULE_ENABLED=true
#   SAFETY_PERSONAL_ONLY=true
powershell -File .\scripts\windows\start-serve-watch.ps1
```

健康检查（脚本默认端口）：`http://127.0.0.1:18088/health`  
（直接 `python cli.py serve` 时端口以 `.env` 的 `PORT` 为准，示例多为 `8088`。）

开机自启 + 每 15 分钟健康巡检（`pythonw`，无弹窗）：

```powershell
powershell -File .\scripts\windows\install-autostart.ps1
powershell -File .\scripts\windows\install-serve-healthcheck.ps1
```

停进程并禁用巡检：`powershell -File .\scripts\windows\stop-serve-watch.ps1`  
卸载自启（含巡检）：`powershell -File .\scripts\windows\uninstall-autostart.ps1`

## CLI 速查

```powershell
# 多 App 配置/密钥/盯盘覆盖（只读）
python cli.py apps-ready

# iOS 凭证 / IPA 校验（不写商店）
python cli.py apple-check --app-id easelife
python cli.py ipa-check --app-id easelife --ipa "F:\upload-test\xxx.ipa"

# 状态（Android / iOS）
python cli.py status --app-id easelife --platform android --version-code 10428 --no-notify
python cli.py status --app-id easelife --platform ios --no-notify

# Android：内部轨上传
python cli.py upload --app-id easelife --platform android `
  --artifact "F:\upload-test\xxx.aab" --track internal --notify

# Android：正式版上传并送审（默认约 5% 分批；全量加 --rollout 100）
python cli.py upload-submit --app-id easelife --platform android `
  --artifact "F:\upload-test\xxx.aab" `
  --track production --allow-production --notify

# 或用包装脚本（先 -WhatIf 预览，不写商店）
powershell -File .\scripts\windows\release-android.ps1 `
  -AppId easelife -Artifact "F:\upload-test\xxx.aab" -Track production -AllowProduction -WhatIf

# 盯盘：正式版成功后会自动登记；也可手动
python cli.py watch --app-id easelife --platform android `
  --version-code <新码> --once --heartbeat-hours 0 --no-notify
python cli.py watch --app-id easelife --platform ios --once --heartbeat-hours 0 --no-notify
```

代理：Android / Google 通常走 `HTTP(S)_PROXY`；iOS / ASC 默认直连（`APPLE_USE_PROXY=false`）。

## 目录结构

```text
app/
  core/           # 编排、盯盘目标、发版文案、就绪检查、rollout
  stores/         # apple / google / lifecycle / phased / ipa 解析
  notify/         # 飞书通知（运营向风格 D）
  schedule/       # serve 内定时轮询
  feishu/         # 发卡片；个人模式下默认无回调按钮
  main.py         # FastAPI：/health、可选 /feishu/webhook
cli.py
config/apps.yaml
scripts/windows/  # serve 启停、开机自启、健康巡检、发版包装
docs/             # 手册与各 App 说明
secrets/          # .p8 / Google SA JSON（勿提交）
data/             # watch_targets.json（本地运行态，勿提交密钥）
```

## 商店凭证

| 平台 | 需要 |
|------|------|
| iOS | ASC API Key（`.p8` + Key ID + Issuer ID）；角色需能管理构建/提审 |
| Android | Play Console 服务账号 JSON，并授予对应应用发布权限 |

- iOS：`ApiKey_*.p8`（个人）与 `AuthKey_*.p8`（团队）的 JWT `sub` 不同，填错会 401；项目按文件名自动判断。见 [docs/IOS_ASC_SETUP.md](docs/IOS_ASC_SETUP.md)。
- 同一 Apple 团队多 App 共用一套密钥；不同主体（如 boykeep）在 `apps.yaml` 的 `ios.*` 覆盖。
- IPA 上传规划走 **ASC Build Upload API**，Windows 可直传，**不依赖 Mac / altool**（尚未接通）。

## 飞书

- **个人模式（当前默认）**：只用发消息 API 私聊 `FEISHU_OWNER_*`；`FEISHU_WEBHOOK_ENABLED=false`；卡片无操作按钮。
- **给运营切群**：见 [docs/OPS_PERSONAL_ONLY.md](docs/OPS_PERSONAL_ONLY.md)，确认前不要改 `SAFETY_PERSONAL_ONLY`。
- 可选卡片按钮（仅关闭个人模式且开启回调后）：`app_upload_submit` / `app_status`。

## 文档索引

| 文档 | 内容 |
|------|------|
| [SAFE_RUNBOOK.md](docs/SAFE_RUNBOOK.md) | 个人调试零影响现网 |
| [RELEASE_PLAYBOOK.md](docs/RELEASE_PLAYBOOK.md) | 发版固定命令 |
| [IOS_RELEASE.md](docs/IOS_RELEASE.md) | iOS 传包→提审→分发全流程与坑点（上线前必读） |
| [SCHEDULE_WATCH.md](docs/SCHEDULE_WATCH.md) | serve 常驻盯盘 |
| [AFTER_SUBMIT_WATCH.md](docs/AFTER_SUBMIT_WATCH.md) | 提审后盯盘 |
| [MULTI_APP_READY.md](docs/MULTI_APP_READY.md) | 多 App 就绪 |
| [ANDROID_APPS.md](docs/ANDROID_APPS.md) | Android 多 App |
| [IOS_ASC_SETUP.md](docs/IOS_ASC_SETUP.md) | iOS 凭据与校验 |
| [ROADMAP_NEXT.md](docs/ROADMAP_NEXT.md) | 后续扩展 |
| [AI_SUPPORT_INTEGRATION_CONTRACT.md](docs/AI_SUPPORT_INTEGRATION_CONTRACT.md) | 日后并入 ai_support 的字段契约（**不改对方仓库**） |
| [SUBMIT_BUTTON_PLAN.md](docs/SUBMIT_BUTTON_PLAN.md) | 「提审」按钮接入方案（修订版） |

安全调试 CLI：`submit-card`（仅私聊说明）/ `card-run --dry-resolve`（只选型）/ `card-run --track internal`（测轨上传）。详见 SAFE_RUNBOOK。

## 下一步（建议优先级）

1. 接通 iOS **Build Upload API**（Windows 直传 IPA → TestFlight）  
2. 接通 iOS **提审** API，并与现有 `watch` / 飞书通知对齐  
3. boykeep：补齐独立 Apple 团队 `.p8` 后跑通 `apps-ready --app-id boykeep`  
4. 运营化：稳定后再按 OPS 清单评估是否关个人模式、改发群（默认不做）
