# App Upload Auto

独立的 iOS / Android 商店上传与提审工具骨架：支持 CLI、定时轮询审核状态、飞书卡片按钮触发，以及飞书通知。

> 当前仓库为骨架：商店 upload/submit 的真实 API 调用已留好接口与注释，需配置密钥后继续补全。
> 进度：Android 上传/发布/盯盘已可用；iOS 鉴权已打通（`cli.py apple-check`），上传与提审待接入。

## 能力

| 模块 | 说明 |
|------|------|
| `upload` / `submit` | iOS（App Store Connect）+ Android（Google Play） |
| `status` | 查询审核/发布状态（Android、iOS 均已接入真实映射） |
| `apple-check` / `ipa-check` | iOS 凭证自检 / 上传前校验（版本递增、构建号查重、bundle 一致） |
| `schedule` | APScheduler 定时轮询，状态变化才通知 |
| `notify` | 飞书互动卡片 |
| 飞书按钮 | `上传并提审` / `仅查询状态` |

## 个人调试（零影响现网，推荐先这样做）

若要尽快跑通且**不影响现网、消息仅自己可见**，请先看：[docs/SAFE_RUNBOOK.md](docs/SAFE_RUNBOOK.md)。

默认安全开关：`SAFETY_PERSONAL_ONLY=true`（只私聊你、不改现网事件订阅、关闭群通知与回调按钮）。用 CLI 触发；飞书仅作个人结果通知。

## 新建飞书机器人（给运营用时再做）

在 [飞书开放平台](https://open.feishu.cn/app) 创建**企业自建应用**（不要复用 ai_support 的机器人）。

1. **创建应用**  
   - 名称建议：`APP发布助手`  
   - 获取 `App ID`、`App Secret`

2. **权限（尽量最小）**  
   - `im:message` / `im:message:send_as_bot`（发消息）  
   - 如需更新卡片：消息相关读写权限按控制台提示勾选  
   - **不要**默认开审批、多维表格、通讯录全量等无关权限

3. **事件订阅**  
   - 请求地址：`https://<公网域名>/feishu/webhook`  
   - 本地调试可用 ngrok / frp 等把 `http://127.0.0.1:8088` 暴露出去  
   - 保存页面上的 **Verification Token**（以及若启用加密则保存 Encrypt Key）到 `.env`  
   - 订阅事件：`卡片回传交互` / `card.action.trigger`  
   - 完成 URL 校验（服务需先启动，能返回 `challenge`）

4. **机器人能力**  
   - 开启机器人  
   - 发布版本并申请可用范围（测试期可先对本公司）  
   - 把机器人拉进运营群，记下群 `chat_id`（可用开放平台 API 或临时日志获取）

5. **填入本项目**  
   ```bash
   copy .env.example .env
   ```
   填写：
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_VERIFICATION_TOKEN`
   - `FEISHU_DEFAULT_CHAT_ID`

## 本地启动（Windows）

```powershell
cd f:\app-upload-auto
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
copy config\apps.example.yaml config\apps.yaml
# 编辑 .env 与 config\apps.yaml
python cli.py serve
```

健康检查：`http://127.0.0.1:8088/health`

## CLI

```powershell
# iOS 凭证自检（签名 JWT + 调 ASC /v1/apps；默认直连不走代理）
python cli.py apple-check
# 按 App 检查（用于 boykeep 这类独立 Apple 团队）
python cli.py apple-check --app-id boykeep

# iOS 状态查询（真实映射；可指定版本号）
python cli.py status --app-id blurams --platform ios
python cli.py status --app-id blurams --platform ios --version-name 5.1049.126

# iOS 上传前校验（解析 IPA + 与 ASC 比对，不写入任何数据）
python cli.py ipa-check --app-id blurams --ipa "F:\upload-test\xxx.ipa"

# 三 App 对照（本地配置；加 --status 查正式版）
python cli.py apps
python cli.py apps --status

# 正式版提审后盯盘（变化/心跳 → 飞书私聊；不自动上线）
python cli.py watch --app-id easelife --platform android --version-code 10421 --interval 30 --heartbeat-hours 12 --notify

# 上传并提审正式版（需 --allow-production）
# 版本说明默认用配置好的默认文案（-General: Bug fixes and system optimizations.），无需每次传
python cli.py upload-submit --app-id blurams --platform android --artifact D:\build\app.aab --track production --allow-production --notify

# 需要自定义版本说明时再加 --whats-new（可重复传入做多语言）
python cli.py upload-submit --app-id blurams --platform android --artifact D:\build\app.aab --track production --allow-production --whats-new "修复卡顿，优化连接速度" --notify

# 分阶段发布：正式版默认 5%（可不传 --rollout）；全量请显式 --rollout 100
python cli.py upload-submit --app-id blurams --platform android --artifact D:\build\app.aab --track production --allow-production --notify

# 显式指定比例，或审核通过后逐级放量（只能递增；100 转全量）
python cli.py upload-submit --app-id blurams --platform android --artifact D:\build\app.aab --track production --allow-production --rollout 10 --notify
python cli.py release --app-id blurams --platform android --version-code 1952 --track production --allow-production --rollout 20
python cli.py release --app-id blurams --platform android --version-code 1952 --track production --allow-production --rollout 100

# 查询状态（可带 --version-code）
python cli.py status --app-id blurams --platform android --version-code 1952 --notify
```

提审后流程：[docs/AFTER_SUBMIT_WATCH.md](docs/AFTER_SUBMIT_WATCH.md) · 三 App 对照：[docs/ANDROID_APPS.md](docs/ANDROID_APPS.md) · iOS 接入：[docs/IOS_ASC_SETUP.md](docs/IOS_ASC_SETUP.md) · 常驻定时：[docs/SCHEDULE_WATCH.md](docs/SCHEDULE_WATCH.md) · 后续扩展：[docs/ROADMAP_NEXT.md](docs/ROADMAP_NEXT.md)
## 目录结构

```text
app/
  core/service.py      # upload / submit / status 编排
  stores/apple.py      # App Store Connect
  stores/google.py     # Google Play
  feishu/              # 发卡片 + 按钮回调
  notify/              # 飞书通知
  schedule/            # 定时轮询
  main.py              # FastAPI：/feishu/webhook
cli.py
config/apps.yaml
secrets/               # .p8 / Google SA JSON（勿提交）
```

## 商店凭证（与飞书分开）

| 平台 | 需要 |
|------|------|
| iOS | App Store Connect API Key（`.p8` + Key ID + Issuer ID），角色需能管理构建/提审 |
| Android | Play Console 服务账号 JSON，并授予对应应用的发布权限 |

说明：

- iOS 个人密钥（`ApiKey_*.p8`）与团队密钥（`AuthKey_*.p8`）的 JWT `sub` 要求相反，填错恒定 401；本项目自动判断。详见 [docs/IOS_ASC_SETUP.md](docs/IOS_ASC_SETUP.md)。
- **iOS 凭据按 Apple 开发者团队隔离**：同一公司主体的多个 App 共用一套（如 blurams + easelife）；不同主体各用一套，可在 `apps.yaml` 的 `ios.key_id/issuer_id/private_key_path` 按 App 覆盖，留空则回退 `.env` 全局。
- Windows 上 iOS 二进制上传通常需转发到 Mac/CI；骨架已预留 `artifact_url` / 远程 runner 扩展点。  
- 旧项目 `ai_support` 里的 Apple JWT 用于内购查单，**不要直接当上传密钥复用**（权限与用途不同）。

## 飞书按钮约定

按钮 `value.type`：

- `app_upload_submit`：上传并提审  
- `app_status`：查询状态  

字段：`app_id`、`platform`（`ios` / `android` / `both`）

## 下一步实现优先级

1. 配好新飞书应用，跑通 `panel` → 点按钮 → 收到 toast + 群消息  
2. 接通 Google `edits.bundles.upload`（Windows 可先做 Android）  
3. 接通 Apple 提审/状态 API；上传走 Mac runner  
4. 完善审核状态枚举映射与失败重试
