# blurams Android 发布 / 提审流程（个人调试）

easelife 共用同一 Play 服务账号，见 [EASELIFE_ANDROID_RELEASE.md](./EASELIFE_ANDROID_RELEASE.md)。

## Google Play 上「发布 / 提审」是什么

| 步骤 | 含义 |
|------|------|
| `upload` | 上传 AAB，并发布到指定轨道 |
| `release` | 把**已有** versionCode 推进到另一轨道（如 internal → production） |
| `status` / `watch` | 查各轨道状态；正式版 `inProgress` 常表示审核/分阶段发布中 |

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
3. 正式版提审后用 `watch` 跟状态，不要依赖人工反复刷 Console
