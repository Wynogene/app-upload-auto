# blurams Android 发布 / 提审流程（个人调试）

easelife 共用同一 Play 服务账号，见 [EASELIFE_ANDROID_RELEASE.md](./EASELIFE_ANDROID_RELEASE.md)。

## Google Play 上「发布 / 提审」是什么

| 步骤 | 含义 |
|------|------|
| `upload` | 上传 AAB，并发布到指定轨道 |
| `release` | 把**已有** versionCode 推进到另一轨道（如 internal → production） |
| `status` / `watch` | 查各轨道状态；正式版 `inProgress` 常表示审核/分阶段发布中 |

## 分阶段发布（只放量给一部分用户）

`--rollout` 用**百分比**表达，只有 `production` 支持。
正式版**未传** `--rollout` 时，默认先放量 **5%**（可由
`apps.yaml` 的 `android.rollout_percent_default` 或 `.env` 的
`ROLLOUT_PERCENT_DEFAULT` 覆盖；留空字符串则关闭默认分批）。

```powershell
# 提审时不传 --rollout → 自动 5% 分批
python cli.py upload --app-id blurams --platform android `
  --artifact "F:\upload-test\xxx.aab" `
  --track production --allow-production `
  --whats-new "zh-CN=修复若干问题" --notify

# 显式指定比例
python cli.py upload --app-id blurams --platform android `
  --artifact "F:\upload-test\xxx.aab" `
  --track production --allow-production --rollout 10 `
  --whats-new "zh-CN=修复若干问题" --notify

# 审核通过、确认稳定后逐级放量（百分比只能往上加）
python cli.py release --app-id blurams --platform android `
  --version-code 1940 --track production --allow-production --rollout 20
python cli.py release --app-id blurams --platform android `
  --version-code 1940 --track production --allow-production --rollout 100   # 转全量
```

全量必须显式传 `--rollout 100`（正式版默认不再是 100%）。

### 不变量（实测出来的 Google 行为，工具会据此防呆）

| 操作 | 行为 |
|------|------|
| 10% → 20% → 50% → 100% | ✅ 递增 |
| 分批 → 100% | ✅ 结束分批，转全量 |
| 100%（completed）→ 分批 | ⚠️ **Google 接受**，但**已收到更新的用户无法回收**，工具会提示 |
| 50% → 20% | ⚠️ 放量未增加，属无效操作（不产生新审核），工具会防呆跳过 |
| internal/alpha/beta + `--rollout` | ❌ 只有 production 支持 |
| `--release-status halted` | 必须带 `--rollout`（Google 要求 0<比例<100%，100% 会被拒） |

> **实践建议**：想控量就**在提审那一步带 `--rollout 10`**。虽然 Google 也接受把
> 已 100% 的版本改回分批，但那时已经放量给用户的部分收不回来，
> 分批的意义会打折。

工具会在调用 Google API **之前**拦下无效操作，并给出 `reason`
（`fraction_not_increased` / `already_on_track`），不会产生假的「提交成功」，
也不会白传 200MB。

- **internal / alpha / beta**：一般**不需要**商店人工审核，发布后测试员可装。
- **production**：可能进入 **Google 审核**；通过后是否立刻对用户可见，由 Play Console 发布设置（含运营是否开自管式）决定。**本工具不检测该开关。**

## 安全默认

- 默认轨道：`internal`
- 操作正式版必须同时加：`--track production --allow-production`
- 服务账号若只有「测试轨道」权限，正式版会失败——需在 Play Console 勾选 **「发布为正式版」**

## blurams 推荐命令

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1

# 1) 上传到内部测试（已验证过）
python cli.py upload --app-id blurams --platform android `
  --artifact "F:\upload-test\xxx.aab" --track internal --notify

# 2) 查状态
python cli.py status --app-id blurams --platform android --notify

# 4) 将已有 versionCode 推进到正式版（高风险，需显式确认）
# 若该码已在正式版且 status 相同，工具会防呆跳过（不会假「送审成功」）
# python cli.py release --app-id blurams --platform android `
#   --version-code 1940 --track production --allow-production `
#   --whats-new "..." --notify
```

提审后完整流程见 [AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)。

## 抓住「过审」时刻（只读，不影响线上）

盯盘除旧 `tracks.status` 外，还会只读拉取：

`GET .../applications/{package}/tracks/production/releases`

用 `releaseLifecycleState` 区分两种运营模式：

| 自管式 | 过审跳变 | 飞书标题 |
|--------|----------|----------|
| 开启 | `IN_REVIEW` → `APPROVED_NOT_PUBLISHED` | 审核已通过（自管式：尚未对用户开放） |
| 关闭 | `IN_REVIEW` → `PUBLISHED` | 审核已通过并已上架（含分批放量） |

该接口只 GET，不建 edit、不 commit，不会改线上数据。
旧 `inProgress` 无法区分审核中与分批上架；生命周期字段可以。

## 上传错了 / 要撤下来怎么办

**先说最关键的：已上传的 AAB 无法从 Google Play 删除。**

- 「应用软件包浏览器」里没有删除入口；Publishing API 的
  `edits.bundles` 也只提供 `list` / `upload` / `close`，**没有 `delete`**。
- 一旦上传，该 `versionCode` 就被**永久占用**，永远不能再用（防复用 / 防降级），
  即使你把它从所有轨道上撤下来。
- 因此「换一个包重传」时，必须准备**更高 versionCode** 的新 AAB。

能撤的是**轨道上的 release**，不是包本身：

| 当前状态 | 能做什么 |
|----------|----------|
| 草稿（draft，未提交） | 直接丢弃/删除该 release，不产生审核 |
| 已提交 / 审核中 / 待发布 | `--release-status halted` 停发；必要时重新放量 |
| 已 100% 发布给用户 | **无法撤回**，已安装用户收不回来 |

```powershell
# 停发（必须带 --rollout；不给则沿用轨道上当前的放量比例）
python cli.py release --app-id blurams --platform android `
  --version-code 1952 --track production --allow-production `
  --release-status halted --rollout 10
```
## 建议节奏

1. 日常先 `upload` 到 `internal` + `status/watch`
2. 确认包无误后，再用 `release ... --track production --allow-production` 做正式提审/发布
3. 要控量就**在提审那一步**加 `--rollout 10`，通过后再逐级 `--rollout 20 → 50 → 100`
4. 正式版提审后用 `watch` 跟状态，不要依赖人工反复刷 Console
