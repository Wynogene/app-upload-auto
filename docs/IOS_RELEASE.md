# iOS：上传 → 提审 → 分发（上线前必读）

> 目的：在**真实传 IPA / 真提审之前**把整条链路和坑点说清楚。  
> 对照 Android：以前「没传 `--rollout` → 过审后可能直接全量」那种坑，**不能**再拖到快碰到用户才发现。

本工具现状（blurams 已真机验证上传 + 提审；仍默认 dry-run）：

| 步骤 | 本工具 | 人工 ASC |
|------|--------|----------|
| 上传前校验 `ipa-check` | ✅ | — |
| IPA 上传到 TestFlight | ✅ 默认 dry-run；`--execute` 才写 ASC；长传自动刷新 JWT | Transporter / Xcode 仍可用 |
| 提审 `reviewSubmissions` | ✅ 默认 dry-run；`--execute`；默认开 7 天分批 | ASC 网页可提审 |
| 状态 / 分批盯盘 | ✅ 只读；`--execute` 提审成功后自动登记 | — |
| 点「发布给用户 / 全量 / 暂停分批」 | ❌ **永不代点** | 需运营在 ASC 操作 |

---

## 0. 和 Android 最关键的差异（先看这个）

| | Android（Play） | iOS（ASC） |
|--|-----------------|------------|
| 传包产物 | `.aab` | **一个** `.ipa`（App Store 发布签名即可） |
| 传包后先到哪 | 对应轨道（internal / production…） | **TestFlight → iOS 构建**（先 Processing） |
| 传包 = 对用户可见？ | production 上且已发布才可能 | **否**。仅进构建库，未挂版本、未过审、未「发布」则用户不可见 |
| 控量方式 | `--rollout N` **自定义**百分比；正式版默认常 **5%** | **不能自定义 %**。提审前只选「是否开启分批」；开了则固定 7 天曲线 |
| 过审后是否立刻给用户 | 看自管式 + 是否已「发布」+ 放量比例 | 看版本的 **发布方式** + **是否开分批**（见下节） |
| 工具默认会不会「全量」 | 正式版**未传** `--rollout` 时走配置默认（常 5%），**不是**静默 100%（已修） | 提审时默认开启 7 天分批；若 ASC 上关掉分批且「过审后自动」→ 接近全量 |

**Android 踩过的坑 → iOS 对应防坑：**

1. **不要假设「没选分批 = 安全」。** iOS 若发布方式是「过审后自动发布」且**未开启** Phased Release，过审后会尽快对开启自动更新的用户放开，**没有** 5%/10% 这种中间档可调。  
2. **控量只有「开 / 关 7 天分批」**，没有 `--rollout 20`。要更稳：用 **手动发布（MANUAL）**，过审后你再点发布（可再配合分批）。  
3. **分批只覆盖「系统自动更新」用户**；点商店「更新」的用户仍可能直接拿到新版。  
4. **传包成功 ≠ 已上架**；还要：构建处理完成 → 挂到 App Store 版本 → 填 what's New 等 → 提审 →（按发布方式）对用户可见。

---

## 1. 完整流程（商店真实链路）

```text
① 打出 App Store 签名 IPA
        ↓
② 上传 IPA  ──►  App Store Connect
                      │
                      ▼
               TestFlight → iOS 构建
               （Processing → 可用 / 无效）
        ↓
③ 在「App Store」里准备某个营销版本（如 5.1054.53）
   · 选择构建
   · 各语言 what's New（本工具默认与 Android 文案/语言一致：en-US + 通用修复说明）
   · 出口合规 / 广告标识等若需勾选
   · 【关键】设定「发布方式」+「是否分阶段发布」
        ↓
④ 提交审核（Submit for Review）
        ↓
⑤ 审核结果
   · 被拒 → 改完再提
   · 通过 → 见第 2 节「三种发布方式」决定何时碰到用户
        ↓
⑥ （若开了分批）按 Apple 固定曲线抬升；可暂停；第 7 天≈全量（自动更新用户）
   或到 ASC 点「发布给全部用户」提前结束分批
```

### 后台分别在哪看

| 阶段 | ASC 菜单位置 |
|------|----------------|
| 刚传完的包 | **TestFlight → iOS 构建** |
| 要上架的版本元数据 / 提审 | **App Store → iOS 版本**（准备提交 / 审核中 / 待发布…） |
| 分批进度 | 该版本的分阶段发布状态（本工具 `status`/`watch` 也会读） |

文件名不必叫 `appstore.ipa`；**一个**合格的 App Store IPA 即可，不必再传 xcarchive 才能上传成功（符号化可另传，非上传必需）。

---

## 2. 过审后会不会碰到用户：三种发布方式

版本属性 `releaseType`（本工具 status 文案里会写）：

| ASC 发布方式 | 过审后 | 风险 |
|--------------|--------|------|
| **过审后自动发布** `AFTER_APPROVAL` | 通过后自动开始对用户分发 | 最高：若**未开分批** ≈ 对自动更新用户全量；若**开了分批**则走 7 天曲线 |
| **手动发布** `MANUAL` | 停在「待开发者发布」`PENDING_DEVELOPER_RELEASE`，**你点发布前用户拿不到** | 最低：过审后你还有一道闸 |
| **指定时间发布** `SCHEDULED` | 到点才发 | 中等：时间到了仍按是否分批执行 |

### 分阶段发布（Phased Release）— 与 Android `--rollout` 不同

- **提审前二选一**：开 / 不开。  
- **不能**设 5%、20% 这种自定义比例。  
- 固定曲线（仅自动更新用户）：

  Day1 **1%** → Day2 **2%** → Day3 **5%** → Day4 **10%** → Day5 **20%** → Day6 **50%** → Day7 **100%**

- 本工具：**只读**进度并通知；**不会**代点「发布到全部用户」，也**不会**代为暂停/恢复分批。

### 推荐组合（本项目已定）

| 目标 | 设置 |
|------|------|
| **正常发版（已确认）** | **过审后自动发布（`AFTER_APPROVAL`）+ 开启 7 天分批** |
| 首次真传验证、必须零用户 | 临时改 **手动发布**；验证完再改回上项 |
| 要立刻全量 | 关分批，或分批中人工点「发布给全部用户」 |

过审后效果：当天起走 1%→2%→…→100% 曲线（仅自动更新用户），**不是**自定义 5%/20%。  
若误关分批只留自动发布 → 过审后接近全量（务必防呆）。

---

## 2.1 自动化「挂版本」怎么做

商店两层概念：

| 概念 | 含义 | 例子 |
|------|------|------|
| **营销版本** `appStoreVersion` | 大版本号（`CFBundleShortVersionString`） | `5.1054.52` |
| **构建** `build` | 每次上传的包（`CFBundleVersion` 构建号） | `5.1054.52.1`、`.2` |

**挂版本** = 把某个已 **VALID** 的 build，关联到某个 appStoreVersion（网页上的「选择构建」）。

API 顺序（接通提审时按此实现）：

1. 上传 IPA → 轮询构建至可用  
2. 按大版本号 **查找或创建** `appStoreVersion`  
3. 设定 **`releaseType=AFTER_APPROVAL`** + **开启 phasedRelease**  
4. `PATCH .../appStoreVersions/{id}/relationships/build` 挂上目标构建  
5. 写 what's New → `reviewSubmissions` 提审  

同一大版本在 ASC 上通常只有一行「进行中」的版本；多次传包产生的是多个 **build**，不是多个大版本。

### 同一大版本传了多个 IPA，自动挂哪一个？

必须显式约定，禁止静默乱挂：

| 策略 | 做法 | 何时用 |
|------|------|--------|
| **B. 本次上传产物（默认）** | 同一次流水线里 `upload` 返回的 `buildId` 直接给 `submit` | CI 一条龙最稳 |
| **A. 指定构建号** | `--build-number` 或 IPA 内构建号，解析对应 `buildId` 再挂 | 正式发版可复现 |
| **C. 同版本最新 VALID** | 按 `uploadedDate` 取最新可用构建 | 仅当书面约定「以最后一次传包为准」 |

**建议默认 B，允许 A 覆盖。**  
防呆：每次构建号须递增（`ipa-check` 已拦重复）；提审前打印/飞书确认 `versionString + buildNumber + buildId + 自动发布 + 分批=ON`；目标版本已绑别的 build 时，可编辑才允许显式换绑，否则报错，不默默覆盖。

---

## 3. 本工具命令形态

与 Android 对齐，但语义不同；**默认不写商店**：

```powershell
# 只读：传包前必跑
python cli.py ipa-check --app-id easelife --ipa "F:\upload-test\xxx.ipa"
python cli.py status --app-id easelife --platform ios --no-notify

# dry-run：预检 + 上传/提审计划，不写 ASC
python cli.py upload-submit --app-id easelife --platform ios --artifact "F:\upload-test\xxx.ipa" --no-notify

# 真正上传到 TestFlight + 提审（显式）
python cli.py upload-submit --app-id easelife --platform ios --artifact "F:\upload-test\xxx.ipa" --execute --no-notify

# 仅提审已有构建（dry-run / --execute）
python cli.py release --app-id easelife --platform ios --version-name 5.1054.53 --build-id <ASC_BUILD_ID> --no-notify
```

说明：

- `upload` / `upload-submit` / `release` 对 iOS 均需 `--execute` 才写 ASC
- 挂构建默认用「本次 upload 返回的 build_id」
- 提审时会**自动开启分批**（`appStoreVersionPhasedReleases`，INACTIVE→过审后走 7 天曲线）
- 提审前校验：ASC 全部已本地化语言的 what's New 非空；构建已声明出口合规
- `--execute` 上传必须成功拉取 ASC 现状（版本/构建号比对），失败则硬拦
- 提审中途失败时终端会附带**失败摘要**（已做到哪 + 建议命令）；**不自动跳步**
- 发布方式 / 点「发布给用户」仍须人工；工具**永不代点**全量结束分批

**版本说明默认值（已实现解析）：**

- 语言：与 Android 相同，当前 `en-US`  
- 文案：`- General: Bug fixes and system optimizations.`  
- 可用 `--whats-new` 覆盖；详见 [ANDROID_APPS.md](./ANDROID_APPS.md)

**盯盘（已可用）：**

```powershell
python cli.py watch --app-id easelife --platform ios --once --heartbeat-hours 0
# 或常驻 serve（方式 B）
```

会通知：过审待手动发布、已上架、分批第 N 天、暂停/全量等。  
**不会**替你点发布或全量。

---

## 4. 上线前检查清单（请逐项确认）

在第一次真实 `upload` / 网页提审前，书面确认：

- [ ] IPA 已 `ipa-check` 通过（bundle id / 版本递增 / 构建号未占用）
- [ ] ASC 上该版本：**过审后自动发布 + 已开 7 天分批**（本项目默认；勿只开自动、忘开分批）
- [ ] 挂版本目标构建已明确（本次上传 build / 指定构建号），不会误挂同大版本的旧包
- [ ] what's New：各**已有本地化**已填（可用 `python cli.py ios-whats-new --app-id <app>` 预览，确认后再 `--apply`）
- [ ] 出口合规等元数据已填，避免卡在审核
- [ ] 盯盘已挂上（`watch` 或 serve），飞书能收到过审/分批变化
- [ ] 明白：本工具不代点「发布给全部用户」

---

### 提审前补全 what's New（已实现，默认不写商店）

ASC 要求：**该版本上已存在的每一种本地化**都要有非空 what's New，否则无法点提审。  
本工具只处理**已有本地化行**，**不会**给从未本地化的语言新建语言包。

```powershell
# 只读预览（默认）：列出已有语言与将写入的文案
python cli.py ios-whats-new --app-id blurams
python cli.py ios-whats-new --app-id blurams --version 5.1049.126

# 确认无误后再写入（显式 --apply）
python cli.py ios-whats-new --app-id blurams --version 5.1049.126 --apply
```

- 默认文案与 Android 共用（`android.release_notes_default` / `.env`）；可用 `--whats-new` 覆盖  
- 默认只填**空的** what's New；加 `--force` 才会覆盖已有文案  
- 版本须处于可编辑状态（如「准备提交」）；不改构建、发布方式、分批，也不自动点提审

---

## 5. 相关文档

- 凭据与 `apple-check`：[IOS_ASC_SETUP.md](./IOS_ASC_SETUP.md)
- 能力清单与下一步：[ROADMAP_NEXT.md](./ROADMAP_NEXT.md)
- 发版命令收口：[RELEASE_PLAYBOOK.md](./RELEASE_PLAYBOOK.md)
- 提审后盯盘：[AFTER_SUBMIT_WATCH.md](./AFTER_SUBMIT_WATCH.md)
