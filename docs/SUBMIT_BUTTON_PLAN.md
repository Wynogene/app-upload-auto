# 「提审」按钮接入方案（修订版）

> 相对更早一版「直接改 ai_support 发卡 + 同步回调执行」的调整。  
> **硬约束：合并前不修改 `ai_support` 仓库。**  
> 字段契约见 [AI_SUPPORT_INTEGRATION_CONTRACT.md](./AI_SUPPORT_INTEGRATION_CONTRACT.md)。

## 与旧方案对比

| 点 | 旧方案（口头） | 本修订版 |
|----|----------------|----------|
| 改谁的代码 | 先动 `execute_app_version_publish` 加按钮 | **先只动本仓库**；对方仓库留到合并窗口 |
| 按钮形态 | 每卡一个「提审」callback | 不变；另保留「评审链接 / 版本链接」为 **url** 按钮 |
| `platform` | 从多维表带上 | 不变；**禁止缺省 both**（一卡一平台） |
| 包来源 | 点按钮后再想办法拿包 | 契约要求 `artifact_url`；本包先实现 **下载 → zip 选型 → 本地 path** |
| 执行方式 | 回调里直接 `upload_and_submit` | **受理立即 toast**，上传进 **后台任务**（防飞书超时） |
| 联调 | 依赖改现网发卡 | **本仓库自建调试卡** / CLI / 可选独立 HTTP；不碰现网事件 URL |
| iOS | 与 Android 一并提审 | Android 先全开；**iOS 未接通上传/提审前按钮明确拒绝或灰态文案** |
| 合并形态 | 隐式耦合 | 整包迁入 + 薄适配；契约字段不变 |

---

## 目标体验（最终，合并后）

```text
评审通过 → ai_support 发群卡
  [评审链接] [版本链接] [提审]     ← 前两个仍是打开链接；提审是 callback
       │
       ▼ 运营点「提审」+ 确认框
飞书 card.action.trigger
       │
       ▼ value 符合契约（app_id/platform/artifact_url/whats_new/…）
本包：受理 → 下载/解压选型 → 上传提审 → 飞书私聊/群通知结果
       │
       ▼ 成功则登记盯盘（Android production 已有）
```

合并前用同一套 `value` + `handle_card_action`（或其后台封装），只是**卡片由本仓库发出**，不是现网群卡。

---

## 分阶段（推荐）

### 阶段 A — 本仓库闭环（现在就可做，零影响 ai_support）

1. **补齐执行链（相对当前缺口）** ✅
   - `artifact_url` / 本地 path → 下载（HTTP）
   - zip → 唯一 `.aab` / `.ipa` 选型（歧义则失败并列出候选）
   - 再调现有 `AppReleaseService.upload_and_submit`
2. **异步化回调** ✅
   - `handle_card_action`：校验 → 后台线程 → toast「已受理」
   - 结束后 `Notifier` 私聊结果（个人模式）
3. **调试发卡（CLI）** ✅ **安全默认**
   - `python cli.py submit-card ... --track internal` → **仅私聊本人**，默认**无回调按钮**
   - `python cli.py card-run ... --track internal` → 真正执行（默认同轨 internal）
   - `card-run --dry-resolve` → 只下载/选型，**不写商店**
   - `--with-callbacks` 可选，且要求个人模式 + 显式打开回调开关；**禁止改现网事件 URL**
4. **Webhook**
   - 默认 `FEISHU_WEBHOOK_ENABLED=false`
5. **能力门闸** ✅
   - 卡片/card-run：**默认 `track=internal`**；正式轨需 `allow_production=true`
   - `platform=ios`：明确拒绝上传提审
   - 禁止 `platform=both` 提审

**推荐安全联调顺序：**

```powershell
# 1) 只验证 zip/路径选型（不写商店、可不发飞书）
python cli.py card-run --app-id easelife --platform android --artifact "F:\upload-test\pkg.zip" --dry-resolve

# 2) 私聊一张说明卡（不写商店）
python cli.py submit-card --app-id easelife --platform android --artifact "F:\upload-test\app.aab" --track internal

# 3) 上传到 internal（写 Play 测试轨，不发正式版）
python cli.py card-run --app-id easelife --platform android --artifact "F:\upload-test\app.aab" --track internal
```

### 阶段 B — 可选桥接（仍可不改 ai_support 仓库）

**完整安全方案（零影响现网/正式轨）：** [PHASE_B_SAFE.md](./PHASE_B_SAFE.md)

摘要：拆成 B0（契约/预览）→ B1（CLI + dry，主路径）→ B2（独立调试应用点按钮）。  
无更高 versionCode 包时**禁止真上传**；禁止改现网事件 URL、禁止 production。

若暂时要用「现网已发出的版本链接」做人肉联调：

- 运营把链接/字段贴给你，或你本地组 `value` JSON
- 调 CLI；先 `--dry-resolve`
- **不**把现网机器人回调指到本机

### 阶段 C — 合并窗口（唯一允许改 ai_support 的阶段）

1. 本包以 `app_upload_auto/`（或改名后的包）迁入，类似 `webclient_llm_auto/`
2. 在 `execute_app_version_publish` **仅增加**一个 callback 按钮，例如：

```python
buttons = [
  {"text": "评审链接", "url": review_url},
  {"text": "版本链接", "url": delivery_url},
  {
    "text": "提审",
    "type": "primary",
    "confirm": f"确认提审 {product} {env} {local_version}？",
    "value": {
      "type": "app_upload_submit",
      "app_id": mapped_app_id,      # 产品线 → apps.yaml id
      "platform": mapped_platform,  # android / ios
      "artifact_url": delivery_url,
      "whats_new": change_log,
      "version": local_version,
    },
  },
]
```

3. `services_webhook` 里对 `type == app_upload_submit`：**import 本包** `handle_card_action`（或先入队再调），不再复制商店逻辑
4. 下载：优先复用对方已有分享下载；失败再回退本包下载器
5. 通知：可继续本包 `Notifier`，或适配到对方 `send_format_message`（适配层替换即可）

---

## 卡片与按钮细则

| 按钮 | 类型 | 说明 |
|------|------|------|
| 评审链接 | `multi_url` 打开链接 | 保持现网行为 |
| 版本链接 | 同上 | 给人眼看/备用下载 |
| **提审** | `callback` + **确认框** | 唯一写商店入口；value 见契约 |

不建议再加「上传并提审 / 仅查询」两套运营按钮到现网评审卡（调试卡可以保留「查状态」）。现网卡保持 **一个写操作入口**，降低误触。

`app_id` 映射失败、平台无法识别、缺少 `artifact_url` → **不入队**，toast 说明原因。

---

## 后台任务建议状态

| 状态 | 飞书侧 |
|------|--------|
| accepted | toast「已受理，完成后私聊/通知你」 |
| downloading / selecting | 可选进度私聊（可后做） |
| uploading / submitting | 同上 |
| succeeded | 结果卡 +（Android）盯盘登记提示 |
| failed | 错误卡；zip 歧义时附候选文件名列表 |

飞书 callback 必须在数秒内返回，故 **禁止**在请求线程里同步传大包。

---

## 当前进度下的能力边界

| 能力 | 阶段 A 是否可真实写商店 |
|------|------------------------|
| Android 上传 + 正式提审 + 分批 | ✅ 已有 CLI/服务，接按钮即可 |
| Android 盯盘 | ✅ 提审成功后沿用现逻辑 |
| iOS 上传 / 提审 | ❌ 未接通 → 按钮门闸 |
| iOS 状态 / what's New dry-run | ✅ 只读，不走「提审」主路径 |
| 仅 artifact_url 无本地文件 | ❌ 待做下载+选型（阶段 A 必做） |

---

## 明确不做（本方案）

- 合并前修改 `ai_support` 发卡或现网事件订阅
- 个人模式下让陌生人点按钮生效（继续 owner 校验）
- zip 多候选时静默猜一个上传
- 用「提审」代替运营在商店后台点「发布给用户」
- 一卡 `platform=both` 一次提两家商店

---

## 建议落地顺序（本仓库）

1. 下载 + zip 选型模块 + 单测  
2. `handle_card_action` 异步受理 + Android 真链路  
3. CLI `submit-card` 私聊调试卡  
4. iOS 门闸文案；待 IPA API 就绪再打开  
5. 文档与契约保持同步；合并时再改对方三处接线  

验收（阶段 A）：所有者点调试卡「提审」→ toast 已受理 → 私聊成功/失败 → Play 控制台可见版本（测试轨或约定正式轨）。
