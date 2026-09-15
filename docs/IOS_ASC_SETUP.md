# iOS（App Store Connect API）接入与自检

本文记录 iOS 自动化上传/提审所需的凭据、最常见的 401 坑，以及一条命令自检连通性。

## 一、需要的三样东西

在 [App Store Connect → 用户和访问 → 集成 → App Store Connect API](https://appstoreconnect.apple.com/access/integrations/api) 生成密钥后：

| 凭据 | 形如 | 放到哪 |
|------|------|--------|
| Issuer ID | `69a6de7x-xxxx-47e3-e053-5b8c7c11a4d1`（UUID，36 位） | `.env` 的 `APPLE_ISSUER_ID` |
| Key ID | `ABCDE12345`（10 位字母数字） | `.env` 的 `APPLE_KEY_ID` |
| `.p8` 私钥文件 | `ApiKey_ABCDE12345.p8` 或 `AuthKey_XXXXXXXXXX.p8` | `secrets/`，路径写入 `APPLE_PRIVATE_KEY_PATH` |

> 这三样都不是 Apple ID 账号密码，也不是开发者账号 ID，**只能下载一次** `.p8`，丢了就撤销重建。

## 二、必须知道的 401 坑：两类密钥的 `sub` 声明互斥

Apple 的 API 密钥分两类，JWT 要求**正好相反**：

| 密钥类型 | 下载文件名 | JWT `sub` | 用途 |
|----------|-----------|-----------|------|
| 团队密钥 Team Key | `AuthKey_<KEYID>.p8` | **不能带** `sub` | 团队级自动化 |
| 个人密钥 Individual Key | `ApiKey_<KEYID>.p8` | **必须带** `sub="user"` | 绑定某个 ASC 用户，继承其角色 |

填错的表现是 `401 NOT_AUTHORIZED` + `Authentication credentials are missing or invalid.`，
和「密钥被撤销」「Issuer/Key 不匹配」的报错**完全一样**，极易误判为密钥失效。

本项目自动判断，无需手动配置：

- `.p8` 文件名以 `ApiKey_` 开头 → 自动加 `sub="user"`
- `.p8` 文件名以 `AuthKey_` 开头 → 自动不带 `sub`
- 想强制覆盖：`.env` 里设 `APPLE_TOKEN_SUB=user`（强制带）或 `APPLE_TOKEN_SUB=none`（强制不带）

实现见 `app/config.py::resolve_apple_token_sub`，回归测试见 `tests/test_apple_token_sub.py`。

## 三、连通性自检

```powershell
cd F:\app-upload-auto
.\.venv\Scripts\python.exe cli.py apple-check
```

正常输出（当前账号 16 个 App）：

```
[OK] ASC 连通正常：可见 16 个 App（sub=user, use_proxy=False）
  com.blurams.ipc | blurams | asc_id=1445991661
  com.vitec.easelifeEn | Ease Life - Smart Camera | asc_id=1588050679
  ...
```

失败时直接抛错并给出可执行的原因，例如：

```
Error: 401 NOT_AUTHORIZED：网络已通，但鉴权失败。常见原因：① 个人密钥缺 sub="user"
（或团队密钥误加 sub） ② Key ID / Issuer ID 与 .p8 不匹配 ③ 该密钥已被撤销或过期
```

加 `--json-out` 可拿到结构化结果，便于脚本消费。

### 网络与代理

ASC 默认**直连**（`trust_env=False`，不继承 `HTTP_PROXY`/`HTTPS_PROXY`）。
飞书/Google Play 那边仍走 `.env` 里的代理，互不影响。确需给 ASC 挂代理时设 `APPLE_USE_PROXY=true`。

## 四、一个 Apple 开发者团队 = 一套凭据（重要）

**API 密钥是按 Apple 开发者团队（公司主体）隔离的**，不是按 App。所以：

| 场景 | 需要几套凭据 |
|------|--------------|
| 同一主体下的多个 App（如 blurams + easelife） | **1 套**即可：`Issuer ID` 共用，`Key ID`/`.p8` 复用或各建一把都行 |
| 不同主体（如 boykeep 是另一家公司） | **各 1 套**，密钥互不通用 |

关键点：`Issuer ID` 是**团队级**的。团队 A 的密钥拿去查团队 B 的 App，结果一定是 `404`
（不是 403），因为那些 App 对该密钥**根本不可见**。这也意味着 boykeep 的
`Issuer ID` / `Key ID` / `.p8` 大概率与 blurams/easelife **完全不同**。

### 配置方式：按 App 覆盖，缺省回退全局

与 Android 的 `android.service_account_json` 完全对称。`config/apps.yaml`：

```yaml
apps:
  - id: blurams                      # 同主体：留空 → 用 .env 全局凭据
    ios:
      app_store_app_id: "1445991661"
      key_id: ""
      issuer_id: ""
      private_key_path: ""

  - id: boykeep                      # 独立主体：另外填一套
    ios:
      app_store_app_id: "待确认"
      key_id: "从 boykeep 团队下载的 Key ID"
      issuer_id: "boykeep 团队的 Issuer ID"
      private_key_path: "secrets/ApiKey_xxx.p8"
```

留空 = 回退到 `.env` 的 `APPLE_KEY_ID` / `APPLE_ISSUER_ID` / `APPLE_PRIVATE_KEY_PATH`；
也可按 App 覆盖 `token_sub`（个人/团队密钥判定）。`.p8` 放 `secrets/`，相对路径按仓库根目录解析。

按 App 自检：

```powershell
python cli.py apple-check --app-id boykeep
# [OK] ASC 连通正常：可见 N 个 App（cred=app, sub=user, use_proxy=False）
#        ^^^^^^^^^ cred=app 表示用的是 apps.yaml 里该 App 的凭据；cred=env 表示回退全局
```

## 五、boykeep：已确认是另一个 Apple 开发者团队

同一 Apple ID 登录 [ASC](https://appstoreconnect.apple.com) 后，**左上角可切换公司主体**：
blurams + easelife 属一个主体，boykeep 属另一个。这是「两个独立团队」，与 Android 侧
独立 Play Console 的情况一致。

因此 boykeep **必须单独申请一套凭据**（切到 boykeep 主体后再生成）：

1. 在 ASC 左上角把主体**切换到 boykeep**
2. 进 [用户和访问 → 集成 → App Store Connect API](https://appstoreconnect.apple.com/access/integrations/api)
   （注意：这里显示的 `Issuer ID` 是**团队级**的，boykeep 的与 blurams 的不同）
3. 生成一把新的 API 密钥 → 记下 **Key ID** 和页面顶部的 **Issuer ID**，下载 `.p8`（**只能下载一次**）
4. 确认你在 boykeep 团队下的角色不低于 `App 管理`（上传构建 + 提审需要；只读角色会 403）
5. `.p8` 放进 `secrets/`，在 `config/apps.yaml` 的 boykeep 条目填上：

```yaml
  - id: boykeep
    ios:
      app_store_app_id: "在 boykeep 主体里该 App 的 Apple ID（数字）"
      key_id: "boykeep 团队的 Key ID"
      issuer_id: "boykeep 团队的 Issuer ID"
      private_key_path: "secrets/ApiKey_xxxx.p8"
```

6. 复验：

```powershell
python cli.py apple-check --app-id boykeep
# 期望：cred=app，且可见列表里出现 com.boykeep.ipc1
```

> 拿 blurams 的密钥查 boykeep 的 App 会返回 **404**（不是 403），因为该 App 对这把密钥根本不可见。
> `apple-check --app-id boykeep` 现在会直接给出这个提示。做多主体时最容易在这里绕圈。

### 对照表

| app id | bundle id | Apple 团队 | 凭据来源 |
|--------|-----------|-----------|----------|
| blurams | `com.blurams.ipc` | 主体 A（与 easelife 同） | `.env` 全局 |
| easelife | `com.vitec.easelifeEn` | 主体 A | `.env` 全局 |
| boykeep | `com.boykeep.ipc1` | **主体 B（独立）** | `apps.yaml` 的 `ios.*` 覆盖 |

## 六、状态查询（已可用）

iOS 的审核/发布状态现在返回真实映射，不再是骨架：

```powershell
python cli.py status --app-id blurams --platform ios
python cli.py status --app-id blurams --platform ios --version-name 5.1049.126
```

输出会带上「版本状态 + 关联构建的处理状态」：

```
[released] ios/blurams: 5.1049.125：已上线（构建 5.1049.125.3：可用）
```

Apple 的原始状态与项目内 `ReviewState` 的映射（`app/stores/apple_states.py`）：

| ASC 状态 | 含义 | 本项目 |
|---|---|---|
| `PREPARE_FOR_SUBMISSION` | 准备提交（尚未提审） | `draft` |
| `WAITING_FOR_REVIEW` | 等待审核 | `waiting_for_review` |
| `IN_REVIEW` | 审核中 | `in_review` |
| `PENDING_DEVELOPER_RELEASE` | 已通过，等你手动发布 | `approved` |
| `READY_FOR_SALE` / `READY_FOR_DISTRIBUTION` | 已上线 | `released` |
| `REJECTED` / `METADATA_REJECTED` / `INVALID_BINARY` | 被拒 | `rejected` |
| `DEVELOPER_REJECTED` | 开发者撤回 | `canceled` |

两个值得注意的点：

- Apple 有新旧两个字段并存（`appStoreState` 旧 / `appVersionState` 新），本工具优先取新字段，两个都能解析。
- 状态为 `approved` 且原始值为 `PENDING_DEVELOPER_RELEASE` 时，提示语会明确提醒**这是「手动发布」模式，需你到 ASC 点发布才对用户生效**（对应 Google Play 的自管式发布）。

### 分批发布（Phased Release）

iOS **不能自定义百分比**。提审前只选是否开启 7 天分批；开启后按固定曲线自动抬升
（1%→2%→5%→10%→20%→50%→100%，仅对自动更新用户）。

`status` / `watch` 会只读拉取：

`GET /v1/appStoreVersions/{id}/appStoreVersionPhasedRelease`

并在文案中带上 `ACTIVE/PAUSED/COMPLETE` 与第几天（约比例）。盯盘仅在以下变化时通知：

| 跳变 | 通知含义 |
|------|----------|
| 审核中 → `PENDING_DEVELOPER_RELEASE` | 过审、手动发布尚未上架 |
| 审核中 → `READY_FOR_*` | 过审并已上架（可含分批开始） |
| 分批第 N 天 → 第 N+1 天 | 分批进度更新 |
| → `PAUSED` / `COMPLETE` | 暂停或已全量 |

全程只读，不写 ASC。

## 七、上传前校验（已可用，零写入）

`ipa-check` 会在真正上传前拦下必然失败的包——IPA 动辄几百 MB，Apple 还要再处理一轮，
传错代价很高：

```powershell
python cli.py ipa-check --app-id blurams --ipa "F:\upload-test\xxx.ipa"
```

三道**阻断**级校验（不通过则拒绝上传）：

| 校验 | 触发场景 | 为什么必须拦 |
|---|---|---|
| bundle id 一致 | 把 easelife 的包传给 blurams | Apple 会直接拒绝，浪费一次上传 |
| 版本号递增 | IPA 版本 ≤ 线上版本 | App Store 硬性要求版本号递增 |
| 构建号不重复 | `CFBundleVersion` 已存在 | Apple 不允许同一构建号重复上传，**即使旧构建已过期** |

另有提示级（不拦截）：该版本已在审核中、该版本号已有 ASC 记录等。

实测四种情况（对着真实 ASC 跑的）：

```
IPA: bundle=com.blurams.ipc version=5.1049.125 build=5.1049.125.9
[阻断] IPA 版本号 `5.1049.125` 未高于线上版本 `5.1049.125`。App Store 要求版本号必须递增…

IPA: bundle=com.blurams.ipc version=5.1049.126 build=5.1049.125.3
[阻断] 构建号 `5.1049.125.3` 在 App Store Connect 中已存在…

IPA: bundle=com.blurams.ipc version=5.1049.126 build=5.1049.126.1
上传前校验通过
[OK] 可以上传

IPA: bundle=com.vitec.easelifeEn version=5.1054.52 build=5.1054.52.1
[阻断] IPA 的 bundle id 是 `com.vitec.easelifeEn`，但 apps.yaml 配置的是 `com.blurams.ipc`…
```

报告会把**所有**问题一次性列出，不用来回试。加 `--json-out` 可拿到结构化结果。

> IPA 解析只读 zip 里的 `Payload/<App>.app/Info.plist` 这一个 entry，不解压整个包，因此很快；
> Watch 扩展里的嵌套 `Info.plist` 会被正确忽略。实现见 `app/stores/ipa_meta.py`。

## 八、当前进度

| 能力 | 状态 |
|------|------|
| 密钥签名 + ASC 鉴权连通（主体 A） | ✅ 已打通 |
| `apple-check` 自检（支持按 App + 可见性校验） | ✅ 已提供 |
| 一主体多 App 共用 / 多主体各一套凭据 | ✅ 已支持（`apps.yaml` 的 `ios.*` 可覆盖） |
| 状态查询 / 盯盘（真实状态映射） | ✅ 已可用 |
| 上传前校验（`ipa-check`） | ✅ 已可用 |
| IPA 上传到 TestFlight（Build Upload API） | ⏳ 下一步（**Windows 可直传**，见下） |
| 提审（`reviewSubmissions` API） | ⏳ 待接入 |
| boykeep（主体 B）凭据 | ⏳ 待生成 `.p8` 后填入 |

关于 IPA 上传：已实测 `POST /v1/buildUploads` 允许 `CREATE`
（Apple 返回 *Allowed operations are: CREATE, DELETE, GET_INSTANCE*），
意味着**不需要 Mac / altool**，Windows 上可直接走 API 上传。
