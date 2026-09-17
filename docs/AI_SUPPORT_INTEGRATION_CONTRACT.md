# 与 ai_support 对接契约（本仓库单方约定）

> **约束：在明确合并之前，不修改 `F:\NNE\ai_support` 任何代码。**  
> 本文只约束 **本仓库** 的对外接口与字段，方便以后整包迁入时接线。  
> `ai_support` 侧的发卡 / webhook 改动留到合并阶段再做。

## 目标

合并后希望做到：

1. 本仓库目录整包迁入（类似现有 `webclient_llm_auto/`），业务逻辑不重写。
2. `ai_support` 只负责：多维表 → 发卡 → 收 callback → **按本契约传参**。
3. 写商店 / 盯盘 / zip 选型仍由本包执行。

合并前若需联调：用 **HTTP** 或 **子进程 CLI** 桥接（见下文），同样不改 `ai_support` 仓库也可在本机手动模拟。

---

## 稳定入口（本仓库保证）

| 入口 | 路径 | 用途 |
|------|------|------|
| 服务门面 | `app.core.service.AppReleaseService` | 上传 / 提审 / 状态 |
| 卡片回调 | `app.feishu.actions.handle_card_action` | 飞书 `card.action.trigger` |
| CLI | `cli.py` | 运维与子进程桥 |
| HTTP | `cli.py serve` → `POST` 事件里调 `handle_card_action` | 进程隔离联调 |

合并时若顶层包名 `app` 与 Flask 冲突，允许**仅改包名**为 `app_upload_auto`，上述模块相对路径不变。

---

## 卡片按钮 `value` 字段表（契约核心）

飞书交互按钮的 `value`（JSON object）必须可被 `handle_card_action` 直接消费。

### 动作类型 `type`

| `type` | 含义 | 本仓库常量 |
|--------|------|------------|
| `app_upload_submit` | 下载/定位包 → 上传 → 提审（按平台能力） | `ACTION_UPLOAD_SUBMIT` |
| `app_status` | 只读查审状态并通知 | `ACTION_STATUS` |

### 提审 `app_upload_submit`

| 字段 | 必填 | 说明 | 与多维表建议映射（合并时再用） |
|------|------|------|--------------------------------|
| `type` | ✅ | 固定 `app_upload_submit` | — |
| `app_id` | ✅ | 本仓库 `config/apps.yaml` 的 id：`blurams` / `easelife` / `boykeep` | 由「产品线」映射，**不是**中文产品名原样 |
| `platform` | ✅ | `android` / `ios`（亦接受 `google` / `apple` / `iphone`）；缺省按 both（不推荐发卡时缺省） | `平台（Android/iOS等）` |
| `artifact_url` | 推荐 | 版本包下载链接（可为 zip / 直链 aab/ipa） | `版本链接` |
| `artifact_path` | 可选 | 本地已落盘路径；与 url 二选一，有 path 优先 | 下载完成后由适配层填入 |
| `whats_new` | 可选 | 版本说明纯文本；缺省走本仓库 release notes 回退 | `递交改动点` |
| `version` | 可选 | 展示/校验用版本号（契约预留；执行器逐步校验） | `版本号` |
| `track` | 可选 | 仅 Android；正式发版常用 `production` | 默认生产策略由 CLI/服务决定 |
| `operator_open_id` | — | 由事件里操作者注入，不必写在按钮 value | 飞书事件 |

**最小可用示例：**

```json
{
  "type": "app_upload_submit",
  "app_id": "easelife",
  "platform": "android",
  "artifact_url": "https://example.com/share/xxx",
  "whats_new": "修复卡顿与崩溃"
}
```

### 状态 `app_status`

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | ✅ | `app_status` |
| `app_id` | ✅ | 同上 |
| `platform` | 推荐 | `android` / `ios` |

### 返回值（给飞书 toast）

`handle_card_action` 返回：

```json
{
  "toast": {
    "type": "success|error|info",
    "content": "...",
    "i18n": { "zh_cn": "..." }
  }
}
```

详细结果走本仓库 `Notifier`（当前默认个人模式私聊）。合并后可再换适配通知，**不必改商店逻辑**。

---

## 产品线 → `app_id` 映射（本仓库约定）

发卡侧合并时按此表转换（可在本仓库维护，避免散落魔法字符串）：

| 多维表「产品线」常见值 | `app_id` |
|------------------------|----------|
| EaseLife / easelife | `easelife` |
| Blurams / blurams | `blurams` |
| Boykeep / boykeep | `boykeep` |

未命中 → **不要自动提审**，toast 提示人工选 app。

---

## 包链接为 zip 时的选型规则（本仓库实现）

下载后若为 zip，按平台扫描（递归）：

| 平台 | 自动选用 | 失败策略 |
|------|----------|----------|
| Android | 唯一 `.aab`；或多个时匹配 `apps.yaml` 的 package + 版本信息 | 0 个或多个无法唯一判定 → 失败并列出候选，不静默上传 |
| iOS | 唯一 `.ipa`；或多个时优先名称含 appstore / 匹配 bundle | 同上 |

排除：`dSYM`、`.xcarchive`、文档、明显 debug 重复包等。

> 卡片 / `card-run` 会先 `resolve_artifact`（下载 + zip 选型）再填本地 path 调用商店 API。  
> 群晖等特殊分享链若通用 HTTP 失败，合并后再接对方下载器。

## 安全调试 CLI（本仓库）

| 命令 | 写商店？ | 飞书 |
|------|----------|------|
| `submit-card` | 否 | 仅私聊本人说明卡 |
| `card-run --dry-resolve` | 否 | 否（终端输出） |
| `card-run`（默认 `--track internal`） | 仅测试轨 | 结果私聊本人 |
| `card-run --track production --allow-production` | 正式轨 | 同上（慎用） |

---

## 合并前联调（不改 ai_support 仓库）

### A. 子进程（最简单）

在本机手动或临时脚本：

```powershell
python cli.py upload-submit --app-id easelife --platform android `
  --artifact "F:\upload-test\xxx.aab" `
  --whats-new "修复卡顿" --notify
```

`artifact_url` 场景：先自行下载 zip → 按上表选出 aab/ipa → 再调 CLI。

### B. HTTP（进程隔离）

1. 本仓库：`SAFETY_PERSONAL_ONLY=true` 下 `python cli.py serve`
2. 用飞书事件 JSON 或本地 curl 模拟 `card.action.trigger`，body 中 `action.value` 符合上表
3. **不要**改现网 `ai_support` 的事件订阅指向，除非明确切换窗口

### C. 同进程 import（仅合并后）

```python
# 以下代码属于未来合并进 ai_support 时的适配示例，当前不要合入 ai_support
from app_upload_auto.app.feishu.actions import handle_card_action

def on_submit_button(value: dict, operator_open_id: str | None):
    return handle_card_action(value, operator_open_id=operator_open_id)
```

发卡处（未来改 `execute_app_version_publish`）增加 callback 按钮，`value` 填本契约字段；**评审链接 / 版本链接** 可继续保留为 url 按钮。

---

## 职责划分（合并后）

| 职责 | 归属 |
|------|------|
| 多维表监听、评审通过发卡 | `ai_support` |
| 按钮 value 组装（本契约） | `ai_support` 适配层 |
| 分享链接下载（可选） | `ai_support` 或本包下载器 |
| zip → aab/ipa 选型 | **本包** |
| 商店上传 / 提审 / 状态 / 盯盘 | **本包** |
| 飞书私聊/群通知 | 本包 `Notifier`；合并后可换适配 |

---

## 本仓库开发纪律（保证可合并）

1. 新能力优先落在 `AppReleaseService` / `handle_card_action` / CLI，避免写死对 `utils_lark` 的依赖。
2. 变更卡片字段时 **先改本文 + `actions.py`**，保持二者一致。
3. 不把密钥、现网 chat_id、群 webhook 写进契约示例。
4. 默认保持 `SAFETY_PERSONAL_ONLY=true`，合并切群另走 OPS 清单。

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-09-17 | 首版：字段表、zip 规则、联调方式；明确不改 ai_support |
| 2026-09-17 | 补充安全 CLI：`submit-card` / `card-run`，默认 internal |
