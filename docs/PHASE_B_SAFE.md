# 阶段 B 详细方案（零影响现网 / 线上）

> 目标：在**不改 `ai_support`、不改现网飞书事件订阅、不写 Play/ASC 正式用户流量数据**的前提下，用人肉参数或本机 HTTP 验证「契约字段 → 下载/选型 →（可选）测轨上传」链路。  
> 与 [SUBMIT_BUTTON_PLAN.md](./SUBMIT_BUTTON_PLAN.md)、[AI_SUPPORT_INTEGRATION_CONTRACT.md](./AI_SUPPORT_INTEGRATION_CONTRACT.md)、[SAFE_RUNBOOK.md](./SAFE_RUNBOOK.md) 配套。

## 0. 硬约束（违反任一条即停）

| # | 禁止 |
|---|------|
| 1 | 修改现网飞书应用的**事件订阅 Request URL** |
| 2 | 修改 / 部署 `ai_support` 主分支或现网进程配置 |
| 3 | 向业务群、评审卡所在群发消息（保持 `SAFETY_PERSONAL_ONLY=true`） |
| 4 | 对**已上架 / 正在对用户放量**的 versionCode 再跑无 dry 的上传 |
| 5 | `track=production` 或 `--allow-production`（阶段 B 全程禁止） |
| 6 | 把本机 `serve` 挂到**现网同一应用**的事件订阅上 |

| # | 必须保持 |
|---|----------|
| 1 | `SAFETY_PERSONAL_ONLY=true` |
| 2 | 默认 `FEISHU_WEBHOOK_ENABLED=false`（仅 B2 临时例外，且只用**独立调试应用**） |
| 3 | Android 仅 `track=internal`（或明确的封闭测试轨，仍禁止 production） |
| 4 | 先 `--dry-resolve`，再决定是否真上传 |

「不影响线上数据」在本方案中的含义：

- **现网系统**：`ai_support`、现网飞书回调、业务群卡片流程 —— **零触碰**  
- **商店对用户侧**：不新增/不推进 **production** 发布；不提高已有正式轨放量  
- **允许的副作用（需你知情）**：若执行真上传，Play **internal** 测试轨会出现新草稿/版本（仅内部测试人员可见，不是对全网用户发版）

若当前**没有**可用于 internal 的更高 versionCode 包：阶段 B **只允许 B0 + B1（dry）**，禁止 B1 真上传与 B2 真提审。

---

## 1. 阶段 B 拆成三档（按风险升序）

```text
B0  纯契约 / 本地模拟     ← 零商店、零飞书现网
B1  CLI + 人肉链接/路径   ← 推荐主路径；默认 dry
B2  独立调试应用 + 本机按钮 ← 可选；绝不碰现网订阅
```

**推荐默认只做 B0 → B1 dry。** 没有新 aab 时到此为止。

---

## 2. B0 — 契约与本地模拟（无商店写、可不发飞书）

### 目的

确认「现网卡将来会传来的字段」能被本包理解，无需真实包上传。

### 步骤

1. 按契约组一份假 `value`（可用记事本）：

```json
{
  "type": "app_upload_submit",
  "app_id": "blurams",
  "platform": "android",
  "track": "internal",
  "artifact_url": "https://example.invalid/demo.zip",
  "whats_new": "B0 dry text",
  "version": "0.0.0-test",
  "preview_only": true
}
```

2. 本机校验字段（不写商店）：

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

# 用已有本地 aab/zip 只做选型（推荐）
python cli.py card-run --app-id blurams --platform android `
  --artifact "F:\upload-test\某个.zip或.aab" --dry-resolve
```

3. （可选）私聊说明卡 / 预览按钮卡 —— **仍不写商店**：

```powershell
python cli.py submit-card --app-id blurams --platform android `
  --artifact "F:\upload-test\xxx.aab" --track internal `
  --preview-only --note "B0 预览"

# 若要看按钮外观（进程内临时开回调开关，勿改 .env 持久化也可）：
$env:FEISHU_ENABLE_CARD_CALLBACKS="true"
python cli.py submit-card --app-id blurams --platform android `
  --artifact "F:\upload-test\xxx.aab" --track internal `
  --with-callbacks --preview-only --note "B0 仅看按钮"
```

### 验收

- `--dry-resolve` 输出 `ok: true` 与选定路径，或歧义时**拒绝**并列出候选  
- 飞书仅你私聊可见；无业务群消息  
- Play Console 无新变更  

### 风险

极低。唯一注意：不要对预览卡去掉 `preview_only` 后去点真提审（且未开本机 webhook 时点了也通常无本机执行）。

---

## 3. B1 — CLI + 人肉参数（主路径）

### 目的

模拟「运营把评审卡上的版本链接 / 改动点抄给你」→ 本机按契约执行。  
**不改对方仓库、不接现网按钮。**

### 3.1 参数从哪来

| 契约字段 | 人肉来源（现网卡 / 多维表，只读抄写） |
|----------|--------------------------------------|
| `app_id` | 产品线 → 自建映射（blurams / easelife / boykeep） |
| `platform` | 卡片「Android / iOS」 |
| `artifact_url` | 「版本链接」（或改为本机已下载路径 `--artifact`） |
| `whats_new` | 「递交改动点」 |
| `version` | 「版本号」 |
| `track` | **固定手写 `internal`**，不要抄成 production |

### 3.2 强制流水线（两步，禁止跳步）

**第一步 — 只解析（必做）**

```powershell
# 本地文件
python cli.py card-run --app-id blurams --platform android `
  --artifact "F:\upload-test\pkg.zip" --dry-resolve

# 或 URL（通用 HTTP；群晖特殊链失败则先浏览器下到本地再走 --artifact）
python cli.py card-run --app-id blurams --platform android `
  --artifact-url "https://你的版本链接" --dry-resolve
```

通过标准：

- 终端 JSON `ok: true`
- package / 文件名与预期 App 一致（可再跑本地 `parse_aab_meta` 对照 `apps.yaml`）
- 若包的 versionCode **已在 production 对用户放量** → **停止**，不得进入第二步  

**第二步 — 仅当有「未上架、可用于测轨」的包时**

```powershell
python cli.py card-run --app-id blurams --platform android `
  --artifact "F:\upload-test\未上架.aab" `
  --track internal `
  --whats-new "B1 internal only"
```

禁止：

```powershell
# 阶段 B 禁止
--track production
--allow-production
```

### 3.3 现网「版本链接」使用规则

| 情况 | 做法 |
|------|------|
| 链接指向**已上架**构建 | 只允许 `--dry-resolve` 或只读 `status`；**禁止** `card-run` 真传 |
| 链接需登录 / 群晖分享失败 | 浏览器手动下载到 `F:\upload-test\`，改用 `--artifact` |
| zip 内多个 aab | dry 应失败并列候选；整理 zip 或改指定单文件后再传 |
| iOS 链接 | dry 选型可以；真提审会被门闸拒绝（预期） |

### 3.4 验收

| 无新包（当前常见） | 有 internal 可用新包 |
|--------------------|----------------------|
| dry-resolve 通过或合理失败 | dry 通过 + internal 上传成功 |
| 私聊可选 | 私聊操作结果 |
| Play production **无变化** | Play **仅 internal** 出现新版本；production 不变 |

### 3.5 风险与缓解

| 风险 | 缓解 |
|------|------|
| 误传已上架包 | 上传前 `status --version-code N --no-notify`；已在 production 则中止 |
| 误开正式轨 | CLI 默认 internal；B 规范禁止 `--allow-production` |
| URL 下错文件 | dry-resolve 先看选定文件名 / package |
| 飞书误进群 | `SAFETY_PERSONAL_ONLY=true` 硬拦 |

---

## 4. B2 — 本地点按钮（可选，最高戒备）

### 目的

验证「飞书 callback → `handle_card_action` → 异步任务」在**本机**打通。  
**不是**把现网评审卡接进来。

### 4.1 架构（正确 vs 错误）

```text
正确：
  [新建「APP发布调试」飞书应用]
       │ 事件订阅 URL = 仅你的隧道 → 本机 127.0.0.1:8088/feishu/webhook
       │ 与现网 ai_support 应用完全分离
       ▼
  你私聊收到 submit-card --with-callbacks --preview-only
       │ 点击
       ▼
  本机 serve 返回 toast（preview_only 不写商店）

错误（禁止）：
  打开现网应用后台 → 把事件 URL 改成 ngrok/本机
  → 现网所有卡片回调失效或打到笔记本  ✗
```

### 4.2 前置清单（全部满足才开）

- [ ] 已单独创建**调试用**飞书应用（名称区分于现网）  
- [ ] 现网应用的事件 URL **未被改动**（打开现网后台核对一眼）  
- [ ] `.env` 里 `FEISHU_APP_ID/SECRET` 指向**调试应用**（或另备 `.env.debug`，勿混用现网密钥去改订阅）  
- [ ] `SAFETY_PERSONAL_ONLY=true`  
- [ ] `FEISHU_OWNER_USER_ID` 仅为你  
- [ ] 第一次点击必须带 `preview_only`  
- [ ] 无 internal 新包时，**永远不要**去掉 `preview_only` 做真提审  

### 4.3 推荐操作顺序

```powershell
# 1) 本机监听（仅绑定本机）
# .env 临时（调试应用专用，勿提交）：
# FEISHU_WEBHOOK_ENABLED=true
# FEISHU_ENABLE_CARD_CALLBACKS=true
# FEISHU_VERIFICATION_TOKEN=调试应用后台复制的 token
# HOST=127.0.0.1
# SAFETY_PERSONAL_ONLY=true

python cli.py serve

# 2) 隧道只绑调试应用的事件订阅（不要动现网应用）
# 例：cloudflared / ngrok http 127.0.0.1:8088
# 在调试应用后台：事件订阅 → 请求地址 → 保存 → 订阅 card.action.trigger

# 3) 先发预览卡
python cli.py submit-card --app-id blurams --platform android `
  --artifact "F:\upload-test\xxx.aab" --track internal `
  --with-callbacks --preview-only --note "B2 按钮联调"

# 4) 私聊点「提审（调试）」→ 应 toast：仅预览，不写商店
# 5) 验完后：关闭隧道、FEISHU_WEBHOOK_ENABLED=false、可删调试应用订阅
```

### 4.4 真按钮上传（仅有新包时）

去掉 `--preview-only`，且 value 仍为 `track=internal`。  
点前再次确认 versionCode 未在 production。  
点后看私聊结果 + Play **internal**。

### 4.5 验收

| 项 | 通过标准 |
|----|----------|
| 现网 | 业务群评审卡、现网机器人回调**行为与改前一致** |
| 预览点击 | toast 表明预览；Play 无变更 |
| 真点击（可选） | 仅 internal 变更；production 无变更 |
| 收尾 | webhook 关闭；隧道关；调试应用可停用 |

### 4.6 风险与缓解

| 风险 | 缓解 |
|------|------|
| 改错现网事件 URL | 清单强制「独立应用」；操作前截图现网 URL |
| 隧道泄露 | 仅短时开启；Verification Token 校验；个人模式拒非 owner |
| 误真提审上架包 | 强制先 preview；无新包禁止去 preview |
| 调试密钥写进现网配置 | 密钥与订阅分离；不在 ai_support 机器上改 |

---

## 5. 与阶段 A / C 的关系

| | 内容 |
|---|------|
| A 未完成「新包 internal 实锤」 | B **不能**用已上架包代替；B 只做 dry + 预览 |
| B 做完 | 仍 **不等于** 现网评审卡可提审；那是 C |
| C | 唯一允许改 `ai_support`：发卡加按钮 + webhook import；届时再谈群通知 |

---

## 6. 一页纸：当前（无更高 aab）怎么做 B

```text
✅ 允许
  - card-run --dry-resolve（本地 aab/zip 或可下载 URL）
  - status --no-notify（只读）
  - submit-card / --preview-only [--with-callbacks 看按钮]
  - （可选）独立调试应用 + serve + preview 点击

❌ 禁止
  - card-run 真传已上架 versionCode（如 blurams 1957）
  - --track production / --allow-production
  - 改现网事件订阅、改 ai_support
  - 往业务群发调试卡
```

### 命令速查（复制用）

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

# B0/B1：只选型
python cli.py card-run --app-id blurams --platform android `
  --artifact "F:\upload-test\com.blurams.ipc_5.1049.4.957_Android_20260916_7835e27eeb_release.aab" `
  --dry-resolve

# 只读确认已上架（不写）
python cli.py status --app-id blurams --platform android --version-code 1957 --no-notify

# 私聊预览卡（可带按钮外观）
$env:FEISHU_ENABLE_CARD_CALLBACKS="true"
python cli.py submit-card --app-id blurams --platform android `
  --artifact "F:\upload-test\com.blurams.ipc_5.1049.4.957_Android_20260916_7835e27eeb_release.aab" `
  --track internal --with-callbacks --preview-only `
  --note "阶段B：仅预览，禁止真传此已上架包"
```

---

## 7. 回滚 / 出事时

1. 立刻停 `serve`、关隧道  
2. `.env` 恢复：`FEISHU_WEBHOOK_ENABLED=false`、`FEISHU_ENABLE_CARD_CALLBACKS=false`  
3. 核对现网飞书应用事件 URL 是否仍为原地址  
4. 若误传了 internal：一般不影响用户；到 Play Console internal 轨查看即可，**不要**用工具去「修复」production  
5. 若误开 production：立即停操作，按运营流程在 Console 人工处理；本工具不提供一键回滚正式轨  

---

## 修订

| 日期 | 说明 |
|------|------|
| 2026-09-17 | 首版：B0/B1/B2 分档 + 无更高 aab 时的禁令清单 |
