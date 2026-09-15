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

| 目的 | 命令 |
|------|------|
| 临时停进程 | `powershell -File .\scripts\windows\stop-serve-watch.ps1` |
| 禁用开机自启（任务还在） | `Disable-ScheduledTask -TaskName AppUploadAuto-ServeWatch` |
| 重新启用自启 | `Enable-ScheduledTask -TaskName AppUploadAuto-ServeWatch` |
| 彻底卸载自启并停进程 | `powershell -File .\scripts\windows\uninstall-autostart.ps1` |

中断后**不影响**商店线上版本，只是本机不再轮询 / 发飞书。

## 行为

- 优先扫描 `data/watch_targets.json` 里**已登记**的盯盘目标（正式版提审成功或 `watch` 会写入）。
- 指纹变化 → 飞书「审核/发布状态变化」（含 Android 过审生命周期、iOS 分批进度）。
- `heartbeat_hours > 0` 时才发心跳；设为 `0` 则只在变化时通知。
- 启动 serve 后会**立刻扫一轮**，之后按 `interval_minutes` 循环。
- Footer 按平台区分 Android / iOS 控制台提示（见 `ANDROID_HINT` / `IOS_HINT`）。

无盯盘目标时，才回退扫全部 app 的轨道 status。

## 本机临时盯盘（不必开 serve）

```powershell
python cli.py watch --app-id easelife --platform android --version-code 10428 --interval 30 --heartbeat-hours 0 --notify
python cli.py watch --app-id easelife --platform ios --interval 30 --heartbeat-hours 0 --notify
```

详见 [AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)。
