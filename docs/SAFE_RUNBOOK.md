# 个人调试安全跑通手册（零影响现网）

目标：尽快验证本项目逻辑，同时满足：

1. 不影响 `ai_support` 等现网功能  
2. 本项目产生的消息 / 调试信息 / 产物仅你个人可见  

## 原则（必须遵守）

| 做 | 不做 |
|----|------|
| 本机 CLI 触发 upload/status | 改现网飞书「事件订阅 URL」 |
| 复用现有应用的 App ID/Secret **仅用于私聊你** | 往任何业务群发消息 |
| 日志写在本仓库 `logs/` | 改 `ai_support` 代码 / 配置 / webhook |
| `SAFETY_PERSONAL_ONLY=true` | 开启群通知、挂公网 webhook 到现网应用 |

## 为什么这样不影响现网？

- **不改**开放平台事件订阅 → 现网卡片按钮仍打到原服务  
- **不改** `ai_support` 仓库 → 现网代码路径零变更  
- 只用发消息 API + `receive_id_type=open_id` → 机器人私聊你，群里其他人看不到  
- 默认关闭 callback 按钮 → 即使误发卡片，也不会把点击导到错误处理链（个人模式直接不发按钮）  
- Webhook 默认关闭 → 本机 `serve` 也不会被飞书现网流量打到  

代价：个人调试阶段 **先不用飞书按钮触发**，用 CLI；通知可以私聊给你看结果。

## 配置步骤

1. `copy .env.example .env`  
2. 填入现有应用的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（只读使用，不要在开放平台改订阅）  
3. 填本人 ID（二选一）  
   - 有 `ou_...` → `FEISHU_OWNER_OPEN_ID=ou_...`（推荐）  
   - 只有企业 uid（如 `50000000`）→ `FEISHU_OWNER_USER_ID=50000000`  
   - 注意：口头说的「飞书 uid」通常是 **user_id**，不是 open_id  
4. 保持：

```env
SAFETY_PERSONAL_ONLY=true
FEISHU_WEBHOOK_ENABLED=false
FEISHU_ENABLE_CARD_CALLBACKS=false
SCHEDULE_ENABLED=false
HOST=127.0.0.1
```

5. 确认机器人对你可见（应用可用范围包含你；必要时先与机器人各发一条消息完成会话）

## 推荐验证命令

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

# 只在终端看结果，不发飞书
python cli.py status --app-id blurams --no-notify

# 结果私聊给你（不会进群）
python cli.py status --app-id blurams --notify

# 上传提审骨架（同样可 --notify 私聊）
python cli.py upload-submit --app-id blurams --platform android --artifact D:\build\app.aab --notify
```

`panel` 在个人模式下会发**无回调按钮**的卡片到你私聊，仅作展示。

## 产物隔离清单

| 产物 | 位置 | 可见范围 |
|------|------|----------|
| 运行日志 | `logs/app-upload-auto.log` | 仅本机 |
| 密钥 | `secrets/`、`.env` | 仅本机（已 gitignore） |
| 飞书消息 | 机器人 → 你的私聊 | 仅你 |
| 商店操作 | 视你用的测试账号/轨道而定 | 建议先用 internal / 测试 App |

商店侧请用**测试包 / internal 轨道 / 非生产版本号**，避免误提审生产。

## 以后要给运营用时再打开的开关

1. 新建专用应用（或独立事件订阅）  
2. `SAFETY_PERSONAL_ONLY=false`  
3. 配置群 `chat_id` + `FEISHU_WEBHOOK_ENABLED=true`  
4. `FEISHU_ENABLE_CARD_CALLBACKS=true`  

在此之前不要动现网事件 URL。

## 提审后盯盘 / 常驻定时

- 本机临时：`python cli.py watch --app-id … --version-code … --notify`（见 [AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)）
- 常驻：`SCHEDULE_ENABLED=true` + `poll_review_status: true` + `python cli.py serve`（见 [SCHEDULE_WATCH.md](./SCHEDULE_WATCH.md)）
- 本工具只做上传/送审与状态通知；**不检测、不依赖「自管式发布」**（调试期可开以免试传对用户可见，后续由运营自行控制）
