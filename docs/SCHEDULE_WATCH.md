# 常驻定时：审核状态轮询（serve）

默认关闭，避免无意刷屏。个人模式下通知仍只私聊本人。

## 开启步骤

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
```

## 行为

- 优先扫描 `data/watch_targets.json` 里**已登记**的盯盘目标（正式版提审成功或 `watch --version-code` 会写入）。
- 指纹变化 → 飞书「审核/发布状态变化」。
- 按目标的 `heartbeat_hours`（默认 12）→ 飞书「审核盯盘心跳提醒」。
- 文案提示到 Play Console 核对进度，并提醒查看 **政策状态**（待办与期限以 Console + 邮件为准；API 读不到该页）。**不检测自管式**；本工具不代点「发布」。

无盯盘目标时，才回退扫全部 app 的轨道 status。

## 本机临时盯盘（不必开 serve）

```powershell
python cli.py watch --app-id easelife --platform android --version-code 10421 --interval 30 --heartbeat-hours 12 --notify
```

详见 [AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)。
