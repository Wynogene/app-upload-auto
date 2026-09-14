# blurams Android 发布 / 提审流程（个人调试）

easelife 共用同一 Play 服务账号，见 [EASELIFE_ANDROID_RELEASE.md](./EASELIFE_ANDROID_RELEASE.md)。

## Google Play 上「发布 / 提审」是什么

| 步骤 | 含义 |
|------|------|
| `upload` | 上传 AAB，并发布到指定轨道 |
| `release` | 把**已有** versionCode 推进到另一轨道（如 internal → production） |
| `status` / `watch` | 查各轨道状态；正式版 `inProgress` 常表示审核/分阶段发布中 |

## 分阶段发布（只放量给一部分用户）

`--rollout` 用**百分比**表达，只有 `production` 支持：

```powershell
# 提审时就分批：先放量 10%（status=inProgress）
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

不带 `--rollout` = 全面发布（100%），与以前行为完全一致。

### 不变量（Google 的硬规则，工具会防呆拦截）

| 操作 | 是否允许 |
|------|----------|
| 10% → 20% → 50% → 100% | ✅ 只能**递增** |
| 50% → 20% | ❌ 放量不能缩水 |
| 100%（completed）→ 20% | ❌ **100% 是终态，不可倒退** |
| 分批 → 100% | ✅ 结束分批，转全量 |
| 测试轨道（internal/alpha/beta）+ `--rollout` | ❌ 只有 production 支持 |

> **想分批，必须「提审时就带上 `--rollout`」。** 一旦以 100% 提交并转为
> `completed`，就无法再改回分批；已经放量到的用户也无法回收。
> 唯一的下行操作是 `--release-status halted`（整体暂停放量并排查），
> 它**不等于**回到某个更低的百分比。

工具会在调用 Google API **之前**拦下这些非法操作，并给出 `reason`
（`already_full_no_downgrade` / `fraction_not_increased`），不会产生
假的「提交成功」，也不会白传 200MB。

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
## 建议节奏

1. 日常先 `upload` 到 `internal` + `status/watch`
2. 确认包无误后，再用 `release ... --track production --allow-production` 做正式提审/发布
3. 要控量就**在提审那一步**加 `--rollout 10`，通过后再逐级 `--rollout 20 → 50 → 100`
4. 正式版提审后用 `watch` 跟状态，不要依赖人工反复刷 Console
