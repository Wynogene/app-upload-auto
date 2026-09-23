# App Upload Auto

独立的 iOS / Android 商店上传、提审与盯盘工具（Python + FastAPI CLI）。

默认 **`SAFETY_PERSONAL_ONLY=true`**：飞书只私聊本人，不改现网事件订阅，不往业务群发消息。  
盯盘 / 状态查询为**只读**；上传与提审会写商店，请先用测试轨或确认版本号。  
iOS 写商店须显式加 **`--execute`**（默认 dry-run）。

## 当前进度（以代码为准）

| 能力 | Android (Google Play) | iOS (App Store Connect) |
|------|------------------------|-------------------------|
| 鉴权 / 连通自检 | ✅ 服务账号 JSON | ✅ `apple-check`（`.p8`） |
| 上传 | ✅ AAB（含正式版；进度 MB/%；卡住可整包重试） | ✅ Build Upload（Windows 直传，勿依赖 Mac；`--execute` 才写） |
| 提审 / 推进轨道 | ✅ `upload` / `upload-submit` / `release` | ✅ `reviewSubmissions` + what's New + 出口合规检查（`--execute`） |
| 分阶段发布 | ✅ 正式版默认约 **5%**（`--rollout` 可调；全量显式 100） | ✅ 提审时默认开启 **7 天分批**（曲线固定，不可自定义 %） |
| 状态 / 过审生命周期 | ✅ `status` + lifecycle | ✅ `status`（含分批进度） |
| 上传前校验 | ✅ AAB 包名 / versionCode 查重等 | ✅ `ipa-check`；`--execute` 时 ASC 现状硬校验 |
| 包来源 | 本地 path；群晖分享链可解析下载 | 同左（`SYNOLOGY_*` / 可选 `AI_SUPPORT_ROOT`） |
| 盯盘通知 | ✅ `watch` / `serve`；正式轨成功后自动登记 | ✅ 同上；**`--execute` 提审成功后自动登记** |
| 运营向飞书文案 | ✅ 风格 D 字段表 | ✅ 版本精确到构建号 |
| 真机验证 | ✅ 多 App 正式轨可用 | ✅ 已用 blurams 更高版本 IPA 跑通上传+提审 |

多 App：`blurams` + `easelife` 共用一套 Play SA / Apple 团队密钥；`boykeep` 为独立主体（Android SA + iOS `.p8` 已配）。

## 个人调试（推荐）

先看：[docs/SAFE_RUNBOOK.md](docs/SAFE_RUNBOOK.md)。

发版固定命令：[docs/RELEASE_PLAYBOOK.md](docs/RELEASE_PLAYBOOK.md)  
多 App 就绪检查：`python cli.py apps-ready` · [docs/MULTI_APP_READY.md](docs/MULTI_APP_READY.md)  
常驻盯盘（方式 B）：[docs/SCHEDULE_WATCH.md](docs/SCHEDULE_WATCH.md)  
巡检 / 切群清单（默认勿切群）：[docs/OPS_PERSONAL_ONLY.md](docs/OPS_PERSONAL_ONLY.md)  
iOS 上线前必读：[docs/IOS_RELEASE.md](docs/IOS_RELEASE.md)

## 本地启动（Windows）

```powershell
cd f:\app-upload-auto
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# 编辑 .env（商店密钥、飞书、代理、可选 SYNOLOGY_*）与 config\apps.yaml
# 常驻盯盘示例：
#   SCHEDULE_ENABLED=true
#   SAFETY_PERSONAL_ONLY=true
# Android 盯盘/上传通常需要本机代理（HTTP_PROXY=127.0.0.1:7892）先开着
powershell -File .\scripts\windows\start-serve-watch.ps1
```

健康检查（脚本默认端口）：`http://127.0.0.1:18088/health`  
开机/重启后若 serve 异常：

```powershell
powershell -File .\scripts\windows\check-serve-watch.ps1
# 仍不通再：
powershell -File .\scripts\windows\start-serve-watch.ps1
```

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
python cli.py apple-check --app-id blurams
python cli.py ipa-check --app-id blurams --ipa "F:\upload-test\xxx.ipa"

# 状态（Android / iOS）
python cli.py status --app-id blurams --platform android --version-code 1959 --no-notify
python cli.py status --app-id blurams --platform ios --no-notify

# Android：内部轨上传
python cli.py upload --app-id blurams --platform android `
  --artifact "F:\upload-test\xxx.aab" --track internal --notify

# Android：正式版上传并送审（默认约 5% 分批；全量加 --rollout 100）
python cli.py upload-submit --app-id blurams --platform android `
  --artifact "F:\upload-test\xxx.aab" `
  --track production --allow-production --notify

# iOS：上传+提审（默认 dry-run；真写商店加 --execute）
python cli.py upload-submit --app-id blurams --platform ios `
  --artifact "F:\upload-test\xxx.ipa" --execute --notify
# 提审成功会自动登记 iOS 盯盘；常驻 serve 已开则一般不必再手动 watch

# 群晖分享链选型（不写商店）
python cli.py card-run --app-id blurams --platform ios `
  --artifact-url "http://delivery.vaas.plus:5000/sharing/<id>" --dry-resolve

# 盯盘：Android 正式轨成功后会自动登记；也可手动
python cli.py watch --app-id blurams --platform android `
  --version-code <新码> --once --heartbeat-hours 0 --no-notify
```

代理：Android / Google 通常走 `HTTP(S)_PROXY`；iOS / ASC 默认直连（`APPLE_USE_PROXY=false`）。长传 IPA 时会自动刷新 ASC JWT（约 20 分钟过期）。

## 目录结构

```text
app/
  core/           # 编排、盯盘目标、发版文案、就绪检查、rollout、群晖分享下载
  stores/         # apple / google / Build Upload / reviewSubmit / lifecycle / phased
  notify/         # 飞书通知（运营向风格 D）
  schedule/       # serve 内定时轮询（启动轮询不阻塞 health）
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
- IPA 上传走 **ASC Build Upload API**，Windows 可直传，**不依赖 Mac / altool**；默认 dry-run，加 `--execute` 才写商店。
- 群晖分享预览页需配置 `.env` 的 `SYNOLOGY_BASE_URL` / `SYNOLOGY_USERNAME` / `SYNOLOGY_PASSWORD`（勿提交真实密码）。

## 飞书

- **个人模式（文档默认建议）**：只用发消息 API 私聊 `FEISHU_OWNER_*`；`FEISHU_WEBHOOK_ENABLED=false`；卡片无操作按钮。
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
| [SUBMIT_JOB_QUEUE.md](docs/SUBMIT_JOB_QUEUE.md) | 提审作业落盘自愈（飞书/serve；零影响成功写路径） |
| [SUBMIT_BUTTON_PLAN.md](docs/SUBMIT_BUTTON_PLAN.md) | 「提审」按钮接入方案（修订版） |

安全调试 CLI：`submit-card`（仅私聊说明）/ `card-run --dry-resolve`（只选型）/ `card-run --track internal`（测轨上传）。详见 SAFE_RUNBOOK。  
阶段 B（人肉/本机联调且不影响现网）：[PHASE_B_SAFE.md](docs/PHASE_B_SAFE.md)。

## 下一步（建议优先级）

1. 与 `ai_support` 合并窗口：按契约接发卡按钮 / 群晖下载（本仓库侧已可独立用 `SYNOLOGY_*`）  
2. 运营化：稳定后再按 OPS 清单评估是否关个人模式、改发群（默认不做）  
3. 作业队列增强：更细阶段、失败告警收敛（见 [SUBMIT_JOB_QUEUE.md](docs/SUBMIT_JOB_QUEUE.md)）  
4. 可选：挂构建防覆盖（默认关；非刚需）  
5. 可选：盯盘卡片展示「开始放量」时间（iOS 有 `startDate`；Android API 弱）
