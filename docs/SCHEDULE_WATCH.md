# 常驻定时：审核状态轮询（serve）

默认需显式开启。个人模式下通知仍只私聊本人。  
**只读轮询**，不写 Google Play / App Store Connect。

## 开启步骤（本机）

1. `.env`：

```env
SAFETY_PERSONAL_ONLY=true
SCHEDULE_ENABLED=true
STATUS_POLL_INTERVAL_MINUTES=30
```

2. [`config/apps.yaml`](../config/apps.yaml)：

```yaml
schedule:
  poll_review_status: true
  interval_minutes: 30
```

3. 启动：

```powershell
cd f:\app-upload-auto
.\.venv\Scripts\Activate.ps1
python cli.py serve
# 或
powershell -File .\scripts\windows\start-serve-watch.ps1
```

默认监听 `127.0.0.1`（脚本里 PORT 默认 `18088`）。

## 开机自启（方式 B：计划任务）

```powershell
cd f:\app-upload-auto
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install-autostart.ps1
# 立刻拉起一次（不必等重新登录）
powershell -ExecutionPolicy Bypass -File .\scripts\windows\start-serve-watch.ps1
```

计划任务名：`AppUploadAuto-ServeWatch`（当前用户登录后自动执行）。

### 人工中断

下面三条仍然可用。`stop` / `uninstall-autostart` 会**一并处理巡检** `AppUploadAuto-ServeHealth`（否则 15 分钟后巡检会把 serve 再拉起）。

```powershell
# 停进程 + 禁用巡检（避免被再拉起）
powershell -File F:\app-upload-auto\scripts\windows\stop-serve-watch.ps1
# 禁用开机自启（任务还在；巡检请一并禁用）
Disable-ScheduledTask -TaskName AppUploadAuto-ServeWatch
Disable-ScheduledTask -TaskName AppUploadAuto-ServeHealth
# 卸载开机自启 + 卸载巡检 + 停进程
powershell -File F:\app-upload-auto\scripts\windows\uninstall-autostart.ps1
```

只禁用 `AppUploadAuto-ServeWatch`、不禁用 Health 时，巡检仍可能把 serve 拉起来。

重新开盯盘：`powershell -File .\scripts\windows\start-serve-watch.ps1`（会重新启用巡检任务，若仍已安装）。

中断后**不影响**商店线上版本，只是本机不再轮询 / 发飞书。

## 行为

- 优先扫描 `data/watch_targets.json` 里**已登记**的盯盘目标（正式版提审成功或 `watch` 会写入）。
- 指纹变化 → 飞书通知（专用标题优先）：过审/待你发布、被拒、Android 放量变更、iOS 分批进度、iOS 合规/合同卡住、构建失效或长时间 PROCESSING。
- **政策状态页**（Play「政策状态」）API 读不到，需人工看 Console + 邮件；工具只在 footer 提示。
- 默认 **不发心跳**（`heartbeat_hours=0`）；仅状态/放量变化时通知。若要心跳：`--heartbeat-hours 12`。
- `heartbeat_hours > 0` 时才发「审核盯盘心跳提醒」。
- 启动 serve 后会**立刻扫一轮**，之后按 `interval_minutes` 循环。
- Footer 按平台区分 Android / iOS 控制台提示（见 `ANDROID_HINT` / `IOS_HINT`）。
- 全程**只读**，不写商店、不代点发布/改放量。

无盯盘目标时，才回退扫全部 app 的轨道 status。

## 本机临时盯盘（不必开 serve）

```powershell
python cli.py watch --app-id easelife --platform android --version-code 10428 --interval 30 --heartbeat-hours 0 --notify
python cli.py watch --app-id easelife --platform ios --interval 30 --heartbeat-hours 0 --notify
```

详见 [AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)。
