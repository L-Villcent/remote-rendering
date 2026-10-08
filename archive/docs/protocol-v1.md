# 渲染任务协议 protocol-v1（r5.1，正式实现基线 + 补丁）

状态：r5 已于 2026-10-08 由双方确认为正式实现基线。r5.1 是 VPS 侧模拟实现中发现的 3 个基线缺陷的补丁（附复现输入和失败的测试），待本机 Codex 确认。未部署。

r5.1 补丁内容（其余部分与 r5 相同）：

| 编号 | 缺陷 | 修改 |
|---|---|---|
| D1 | 权限表没有授予三项必需的读权限：ingest 读 `down/jobs`（XF-R 检查需要 job.json）、ingest 读 `down/control`（取消）、publish 读 `inbox/jobs`（5.4 的 `ignored_terminal` 判断） | security-boundary 4.2 增加三条 ACL；新增机器可读的 `deploy/access-matrix.json` 和测试 SL50、SL51 |
| D2 | 交接报告的接收结论没有消息定义 | result-v1 新增 `$defs.handoff_record`；第 4 节和第 11 节登记它的位置 |
| D3 | result.json 无法解析且尝试未知时，确认记录无法如实填写 `attempt_no` | `ingest_record.attempt_no` 可以为 null，但只限 `verdict = conflict`（5.2） |
依据：计划书 0.2，VPS 评审报告 v0.1，本机 Codex 回应 v0.3，VPS 回复 v0.2–v0.4，本机 Codex《协议 r3 审阅结论》《协议 r4 复验与决定》。
配套文件：
- `docs/security-boundary-v1.md`：身份、权限、沙箱
- `docs/acceptance-v1.md`：验收方法
- `schemas/*.schema.json`：消息格式
- `refimpl/render_protocol/`：规则的参考实现（只用标准库）
- `tests/`：测试，分为 schema、语义、状态机三组

本文中的“必须”“不得”是冻结后的强制要求。schema 只约束单个文档的形状；跨字段规则在第 10 节，状态机在第 5 节，两者都有参考实现和测试。

---

## 1. 范围

- 单 Worker（别名 `worker-local`），同一时刻只有一个活动 GPU 任务。
- 传输：本机 Worker 经 Tailnet 主动连接 VPS 上的受限 SFTP 账户。VPS 是唯一权威文件系统。
- 适配器：`webgl-frames`，初始接口版本 `0.1.0`（只用于双方对接，不代表已完成或已验证）。
- v1 不包括：多 Worker、HTTP 服务、拆帧、音频产物、任意命令执行。

## 2. 角色与身份

| 身份 | 位置 | 职责 | 可信度 |
|---|---|---|---|
| `claude-agent` | VPS 沙箱内 | 写代码；用 renderctl 写提交和取消请求；读 inbox | 模型驱动，不作安全决策 |
| `render-publish` | VPS 系统服务 | 对 outbox 取快照、校验、计算身份、原子发布、写回执 | 管理员安装，Claude 不可改 |
| `render-ingest` | VPS 系统服务 | 对上传取快照、校验、判定、发布到 inbox、写确认 | 同上 |
| `render-netcheck` | VPS 系统服务 | 输出白名单网络摘要 | 同上 |
| `render-worker` | VPS 上的 SFTP 账户 | 本机 Worker 的登录身份 | 半可信 |
| Worker 监督进程 | 本机 | 领取、校验、执行、上传 | 本机 Codex 实现 |
| 渲染子进程 | 本机 | 只执行任务 | 不可信，无凭据，无网络 |

## 3. 标识符与时间

| 标识 | 生成方 | 格式 | 说明 |
|---|---|---|---|
| `job_id` | render-publish | `j_` + 26 位 ULID | ULID 含 **VPS 的 UTC 时间**，由 VPS 生成，不涉及本机 |
| `idempotency_key` | Claude | `^[a-z0-9][a-z0-9._-]{2,63}$` | 请求的名称；不参与内容身份计算，见 6.2 |
| `identity_sha256` | render-publish | 64 位小写十六进制 | 内容身份：规范化提交 + 任务包字节，见 6.2 |
| `attempt_id` | Worker | `a_` + 26 位 Crockford Base32 | **130 位随机数，不是 ULID，不含时间** |
| `attempt_no` | Worker | 1 … `limits.max_attempts` | 判断尝试新旧 |
| `worker_epoch` | Worker | 从 1 递增，持久保存 | Worker 每次启动加 1 |
| `request_id` | render-publish | `req_` + 26 位 ULID | 取消请求 |
| `artifact_id` | Worker，固定语法 | `^(log|keyframe|contact|preview|final)(-[0-9]{1,6})?$`；关键帧为 `keyframe-<frame_index>` | 文件名只由 ID 和固定扩展名组成 |
| `toolchain_id` | 管理员登记 | `^tc-[a-z0-9-]{1,32}$` | 指向 `/etc/render/toolchains.json` 中的完整版本和可执行文件哈希 |

**两种 seq，作用域不同**：

- **心跳 seq**：按 `(worker_epoch, seq)` 字典序比较；新 epoch 从 1 重新计数。
- **状态 seq**：只在同一个 `attempt_id` 内比较，与 epoch 无关。Worker 重启后若继续上报同一尝试（例如补发结果），下一个 seq = max(本机账本记录值, Worker 在 `up/` 中自己上次上传的 status.json 的 seq) + 1。账本采用**先写后传**：每次上传前先把 seq 写入账本并 fsync。

**时间**：所有时间戳由 VPS 服务生成，格式为 `YYYY-MM-DDTHH:MM:SSZ`（UTC，不带小数，不带偏移）。Worker 只发送持续时长（`*_ms`），不发送任何墙钟时间。

## 4. 目录与消息

具体所有者、权限和容量见 `security-boundary-v1.md` 第 4 节。每个文件对应的 schema 定义见第 11 节。

```text
VPS
/srv/agent/outbox/                         claude-agent 写，render-publish 读
  submissions/<idempotency_key>.json         submission
  bundles/<idempotency_key>.tar              任务包
  control/<job_id>.cancel.json               cancel_submission
  handoff/<report_id>.md

/srv/render/                （render-worker 的 chroot 根）
  down/                                    Worker 只读
    jobs/<job_id>/job.json                  job（规范化提交 + 发布字段）
    jobs/<job_id>/bundle.tar
    control/<job_id>/cancel.json            cancel_request
    acks/<job_id>/<attempt_id>.json         ingest_record（结果确认）
    acks/_worker/heartbeat.json             heartbeat_ack
    acks/_handoff/<report_id>.json          handoff_record（r5.1）
    handoff/<report_id>.md
  up/                                      Worker 暂存区（独立容量受限文件系统）
    _worker/heartbeat.json                  heartbeat
    jobs/<job_id>/<attempt_id>/status.json  status
    jobs/<job_id>/<attempt_id>/artifacts/<artifact_id>.<ext>
    jobs/<job_id>/<attempt_id>/result.json  result（最后上传）
    handoff/<report_id>.md
    （.part- 开头的文件是上传中，接收方忽略）

/srv/render-data/inbox/                    claude-agent 只读
  workers/worker-local.json                 worker_view
  jobs/<job_id>/status.json                 job_view
  jobs/<job_id>/<attempt_id>/               已接受的产物 + ingest.json（ingest_record）
  submissions/<idempotency_key>.json        submission_receipt
  submissions/<job_id>.cancel.json          cancel_receipt
  handoff/<report_id>.md
  handoff/<report_id>.record.json           handoff_record（r5.1）
  netcheck/status.json                      netcheck_view
```

## 5. 状态机

状态分三个维度，**分别保存，不合并成一个字段**。参考实现：`refimpl/render_protocol/statemachine.py`；测试：`tests/test_state_machine.py`（ST-xx）。

### 5.1 执行状态（每个尝试，来自 Worker）

`leased → running → uploading → succeeded | failed | cancelled`

ingest 对状态消息的接受规则（按顺序判断）：

| 顺序 | 条件 | 处理 |
|---|---|---|
| 1 | 任务已处于终态 | 忽略（`job_terminal`） |
| 2 | `attempt_no > max_attempts` | 忽略（`attempt_limit_exceeded`） |
| 3 | 同一 `attempt_no` 已有不同 `attempt_id`，或同一 `attempt_id` 已以不同 `attempt_no` 出现 | 忽略（`attempt_conflict`），任务状态不变 |
| 4 | `attempt_no` 小于当前尝试 | 忽略（`superseded_attempt`） |
| 5 | `seq` ≤ 已接受的 seq | 忽略（`stale_seq`） |
| 6 | 该尝试的执行状态已是终态，或已有终局结论 | 忽略（`status_after_terminal`）；**不得以更高的 seq 从终态退回 running** |
| 7 | 状态的等级低于当前（leased 1 < running 2 < uploading 3 < 终态 4） | 忽略（`illegal_transition`）。**允许向前跳跃**，因为 ingest 轮询期间 Worker 可能已覆盖中间状态 |
| 8 | 同一状态下 `frames_done` 减少 | 忽略（`progress_regressed`） |
| — | 其余情况 | 接受，记录 `received_at` |

### 5.2 交付结论（每个尝试，由 ingest 判定）

| 结论 | 条件 | 对任务的影响 | `retry_allowed` |
|---|---|---|---|
| `accepted` | 校验全部通过 | 执行结果成为任务终态；若 `outcome=failed` 且 `retry_scheduled=true` 且 `attempt_no < max_attempts`，则进入 `retry_pending`，不算终态 | false |
| `quarantined` | 任一隐私规则（TX-*）命中，优先于其他原因 | **终态 `quarantined`**；不自动重试，需要人工处理后作为新任务提交 | false |
| `rejected` | 只有可重试原因（`hash_mismatch`、`missing_artifact`、`unreadable`），且该尝试此前被拒次数 < `upload_retry_limit` | 不变；Worker 重传同一尝试 | true |
| `delivery_failed` | 存在不可重试的原因（格式、结构、schema、XF 规则、工具链未登记、超限），或重传次数已用尽 | **终态 `delivery_failed`** | false |
| `stale` | 属于已被取代的尝试、重复交付，或任务已处于终态 | 不变 | false |
| `conflict` | `attempt_no` 冲突或超过上限 | 不变；ingest 只记录，不作任何处理 | false |

**结果与已上报执行终态必须一致**：如果该尝试已经通过状态消息上报了执行终态（例如 `cancelled`），而结果的 `outcome` 与之不同（例如 `succeeded`），则增加原因 `outcome_mismatch`（不可重试），判 `delivery_failed`，已记录的执行状态不被改写。只有中间状态，或执行终态与结果一致时，按正常规则判定。允许向前跳跃（5.1 第 7 条）与禁止改写终态是两条独立的规则（ST26–ST28）。

**无法归属的交付**（r5.1）：result.json 无法解析，且该 `attempt_id` 从未出现过，此时无法确定尝试编号。判 `conflict`，`ingest_record.attempt_no` 写 null，任务状态不变。如果尝试已知，则使用已知的编号，按正常规则判定（例如 `malformed_json` 判 `delivery_failed`）。

Worker 收到 `accepted`、`quarantined`、`delivery_failed`、`stale` 或 `conflict` 时，清理 `up/` 中该尝试的文件并标记“已交付”；收到 `rejected` 时按原因重传。本机文件按本机保留策略处理（隔离的产物保留在本机供用户查看）。

### 5.3 任务显示状态（由 VPS 推导）

| 显示状态 | 条件 |
|---|---|
| `queued` | 已发布，尚未出现任何尝试 |
| `leased` / `running` / `uploading` | 当前尝试的执行状态 |
| `delivering` | 当前尝试的执行状态已是终态，但还没有终局结论 |
| `retry_pending` | 当前尝试的结果已被接受，结果为失败且计划重试 |
| `succeeded` / `failed` / `cancelled` | 当前尝试的结果被接受（**终态**） |
| `quarantined` | 隐私隔离（**终态**） |
| `delivery_failed` | 不可交付（**终态**） |

**规则**：

1. 终态不可逆，之后的任何状态、结果或取消都不会改变它。
2. 叠加标记：`cancel_requested`（存在未处理的取消）；`worker_lost` 和 `unconfirmed`（Worker 健康状态，只出现在活动状态上）。终态没有叠加标记。
3. `worker_lost` **只是显示标记**：不新建尝试，不重新分配，不自动重新提交（ST54）。
4. 提交被拒（`submission_receipt.verdict = rejected | conflict`）不产生任务，只有回执。

### 5.4 取消与终态竞争

| 情形 | 结果 | `job_view.cancel` |
|---|---|---|
| 已处于终态时收到取消 | publish 不发布控制文件，回执为 `ignored_terminal`；状态不变 | `ignored_terminal` |
| 排队中（尚无尝试） | Worker 看到取消后，新建一个尝试并直接上报 `cancelled`（`exit.kind = not_started`） | `honored` |
| 运行中 | Worker 终止整个进程树后上报 `cancelled` | `honored` |
| `retry_pending` 期间 | 取消剩余的全部重试：Worker 不再开始执行，以下一个 `attempt_no` 上报 `not_started` 的 `cancelled` 结果，收敛到终态（ST45） | `honored` |
| Worker 已完成执行，结果尚未被接受 | Worker 照常交付；以接受的结果为终态 | `too_late` |
| 取消与隔离或交付失败竞争 | 终局结论优先 | `too_late` |
| Worker 离线 | 保持叠加标记 `cancel_requested`，**不得**显示为已取消 | `requested` |

Worker 的判定原则：看到取消时，如果执行已经完成，就交付结果；否则终止执行，交付 `cancelled`。

### 5.5 Worker 健康（由 ingest 维护）

- `never_seen` → 收到心跳 → `alive`；超过 90 秒没有接受新心跳 → `lost`；收到更新的心跳 → `alive`。
- 时间判断使用调用方提供的**单调时钟**；重复或更旧的 `(epoch, seq)` 不刷新时间。
- ingest 重启时载入持久化的 `(epoch, seq)`，状态为 `unconfirmed`，直到收到更大的值。
- 心跳由轻量循环处理，与结果的重处理分开；Worker 使用单独的连接发送心跳（详见 6.5）。

## 6. 流程

### 6.1 提交（Claude，沙箱内）

renderctl 把提交写入 `outbox/submissions/`，任务包写入 `outbox/bundles/`。两者都先写临时名，再重命名。renderctl 会先在本地预检 schema 和第 10 节的 XF-J 规则，但**不承担安全校验**。任务包建议用可复现方式打包：条目排序、`mtime=0`、uid/gid 为 0、ustar 格式。这只是 renderctl 的便利措施，不是协议对身份的要求。

### 6.2 发布与内容身份（render-publish，R1）

1. **安全读取 outbox**：与 ingest 相同的快照规则（不跟随符号链接；只接受普通文件；属主须为 claude-agent；链接数为 1；有大小上限；边复制到私有暂存区边计算哈希）。之后只处理快照。
2. **严格解析**：UTF-8，无 BOM；重复键、浮点数、NaN、Infinity 一律拒绝（`malformed_json`）。
3. 校验 schema（submission）、XF-J 规则；对任务包执行 XF-B 规则和 XF-J12；扫描凭据。
4. **规范化**：补齐默认值（`parent_job_id=null`、`params={}`、`final_delivery=retained_local`、`max_upload_mb=200`），并把集合型数组（`artifacts`、`export.profiles`、`render.keyframes`）排序。规范化结果就是写入 job.json 的提交部分。
5. **内容身份**：

   ```text
   identity_sha256 = SHA-256( canonical({
       "domain": "render-protocol/v1/job-identity",
       "bundle_sha256": <快照中任务包的字节级 SHA-256>,
       "submission": <规范化提交，去掉 idempotency_key>
   }) )
   ```

   - `canonical` 是 RFC 8785（JCS）的受限子集：键按 UTF-16 码元排序；没有空白；字符串以 UTF-8 输出，只转义 `"`、`\` 和 U+0000–U+001F；整数限制在 ±(2^53−1)；**协议文档中不允许浮点数**（小数参数用字符串传递）。不依赖任何语言的默认 JSON 序列化。跨语言测试向量见 `examples/protocol/vectors/identity-vectors.json`。
   - 任务包采用**字节级身份**：tar 的时间戳、条目顺序等字节差异都会产生不同的身份。
6. **幂等判定**：

   | 情况 | 结论 |
   |---|---|
   | 新的 `idempotency_key` | 分配 `job_id`，发布，回执为 `published` |
   | 同键，身份相同 | 回执为 `duplicate`，返回原 `job_id`，不重新发布 |
   | 同键，身份不同（提交或任务包任一不同） | 回执为 `conflict`，返回新身份值，不发布 |
   | 任何校验失败 | 回执为 `rejected`，附原因编号 |

7. 在 `down/.staging/` 写完 job.json 和 bundle.tar 后，用一次 `rename` 原子发布到 `down/jobs/<job_id>/`。然后写回执。renderctl 看到回执后删除 outbox 中的原件。

### 6.3 领取与下载（Worker）

1. 每 5–10 秒列举 `down/jobs/`；本机账本中没有的 `job_id` 即为候选。
2. **先建立尝试，再下载**：取得本机排他锁；在账本中创建 `attempt_id` 并写入（先写后传）；上报 `leased`。这样下载或校验失败时，也有一个尝试可用来上报结果。
3. 下载到本机临时目录：job.json 按 stat 得到的大小完整读取，并用严格解析；bundle.tar 校验大小和哈希；然后在本机重新执行 XF-J、XF-B、XF-J12 规则，**Windows 侧必须实现同样的路径规则**（保留设备名、尾点、大小写冲突、重复条目等）。
4. 校验失败时在**同一尝试内**重新下载，最多 `download_retry_limit` 次（第 9.1 节）；仍失败则上报 `failed` / `validation_error`（`exit.kind = not_started`）。如果是 job.json 本身无法读取或解析，`bundle_sha256`、`adapter` 和 `frames.expected` 一律写 null，**不猜测**（10.3 R02/R03/R15）。
5. 校验通过后转入执行（`running`）。

### 6.4 执行

适配器在本机受限环境中执行，时间 `t = frame_index / fps`。渲染子进程不持有 Worker 凭据，不能访问网络。

### 6.5 状态与心跳

- 心跳：每 15 秒，覆盖写入 `up/_worker/heartbeat.json`（先写 `.part-`，再 `posix-rename`）。ingest 接受后写 `heartbeat_ack`。
- 状态：状态变化时上报；运行中至少每 30 秒上报一次，带进度。
- ingest **不读取文件 mtime**；SFTP 层也禁止设置 mtime。接受新的 seq 时，用 VPS 时钟记录 `received_at`。
- **独立性（必须）**：Worker 用独立执行单元和**单独的 SSH 连接**发送心跳，产物上传要限速；ingest 的轻量循环（心跳和状态）与结果重处理分开。渲染卡住或上传大文件都不得延迟心跳。
- **失联不重复执行（必须）**：见 5.3 第 3 条；断网时本机继续渲染，结果落盘，恢复后续传，不新建尝试。

### 6.6 结果上传（Worker）

产物先上传为 `.part-<artifact_id>.<ext>`，再 `posix-rename` 为正式名；`result.json` 最后上传。上传前 Worker 先对结果自检 XF-R 规则。连接中断时按传输层退避重连（第 9.1 节，不计入重试次数），已完成的文件不重复上传。

### 6.7 接收（render-ingest）

1. 只有 `result.json` 存在时才处理该尝试。
2. 快照：同 6.2 第 1 条，属主须为 render-worker。之后只处理快照。
3. 判定顺序：
   1. 尝试身份（5.1 第 1–4 条：终态、上限、冲突、已取代）→ `stale` 或 `conflict`
   2. 严格解析和 schema；未知键报 `schema_invalid`，引用为文件级
   3. XF-R 规则
   4. 产物大小和哈希
   5. 工具链登记
   6. 结构检查（第 7 节）
   7. 文本规则（第 8 节）
4. 用 5.2 的规则得出结论，写 `ingest.json` 和确认文件；`accepted` 时用一次 `rename` 原子发布到 inbox。
5. 结论缓存：以（sha256、规则版本、配置版本）为键；缓存命中时仍做大小和哈希校验。

### 6.8 交接报告

- 本机 → VPS：Worker 上传到 `up/handoff/`，ingest 用文本规则检查后发布到 `inbox/handoff/`。结论写成 `handoff_record`（r5.1），同时写到 `inbox/handoff/<report_id>.record.json` 和 `down/acks/_handoff/<report_id>.json`；被隔离的报告不进入 inbox。Worker 读到记录后清理 `up/handoff/` 中的对应文件。
- VPS → 本机：经 publish 扫描凭据后发布到 `down/handoff/`。
- 只用 Markdown。地址用文字描述，不写字面值。

## 7. 产物与导出配置

| 配置 | 用途 | 格式 | 上传上限 |
|---|---|---|---|
| `png-rgb8-v1` | 关键帧、联系表 | PNG，8 位 RGB/RGBA，非隔行 | 每文件 8 MB |
| `h264-preview-v1` | 预览 | MP4/H.264，无音轨 | 20 MB，≤ 20 秒 |
| `h264-final-v1` | 正式视频 | MP4/H.264，无音轨 | 选择上传时：200 MB，≤ 600 秒。**本地保留时不受上传上限约束** |

### 7.1 结构指纹（规则，不是样例序列）

- 指纹是**允许的结构文法加重复次数范围**，不是某个样例的精确类型序列。例如 PNG：`IHDR{1} (sRGB|gAMA|pHYs|cHRM){0,4} IDAT{1,8192} IEND{1}`；MP4 包括顶层顺序的几种允许变体、每层允许的子盒子及次数范围、`hdlr` 名称集合、元数据键名集合。
- 首次验证（MX-01）用**样例矩阵**生成候选文法：每个配置至少 3 种分辨率 × 2 种时长 × 2 种画面复杂度。文法由用户确认后登记。
- 结构检查**只能发现实际的结构差异**。工具链变化由 `toolchain_id` 登记触发重新验证；版本不同但结构相同的情况，结构检查发现不了，也不要求它发现（MX-04）。

### 7.2 每文件结构检查（有界只读）

**定位**：发现产物偏离已验证配置的情况，**不证明文件不含敏感信息**。不解码，不重新编码，不清洗。

| 格式 | 遍历方式 |
|---|---|
| PNG | 校验 8 字节签名。每个块：读 8 字节（4 字节长度 + 4 字节类型）；IHDR 读其 13 字节载荷；其他块用 seek 跳过“长度 + 4 字节 CRC”。一直遍历到 IEND，并检查 IEND 之后有无尾随字节。每块结构开销按 12 字节计入预算 |
| MP4 | 遍历全部顶层盒子头（8 字节，或 `size=1` 时 16 字节）；`moov` 不论在文件开头还是末尾，都整体读入（受上限约束）并递归解析；`mdat` 只读头部。检查 `udta`、`meta`、`hdlr` 名称、`uuid`、有载荷的 `free`/`skip`，以及尾随字节 |

| 上限 | PNG | MP4 预览 | MP4 正式（上传时） |
|---|---|---|---|
| 文件大小 | 8 MB | 20 MB | 200 MB |
| 结构数量 | ≤ 8192 块 | 顶层 ≤ 16；`moov` 内 ≤ 20000；深度 ≤ 10 | 顶层 ≤ 16；`moov` 内 ≤ 100000；深度 ≤ 10 |
| 单个结构 | 非 IDAT 块载荷 ≤ 64 KB；长度不越界 | `moov` ≤ 4 MB；长度不为 0、不越界 | `moov` ≤ 8 MB；长度不为 0、不越界 |
| 实际读取字节数 | ≤ 128 KB | ≤ 4.2 MB | ≤ 8.2 MB |
| 时间 | 每文件 2 秒 | 每文件 2 秒 | 每文件 5 秒 |

每个尝试的结构检查累计不超过 30 秒。超过任一上限判 `structure_limit_exceeded`（不可重试）。数值只是上限，实际开销按 MX-05 实测。

## 8. 文本规则（rules v1）

适用于：日志、报告、JSON 中的所有字符串值。

| 规则 | 拦截对象 |
|---|---|
| `TX-IPV4` | 每段 0–255 的四段点分数字。**自由文本中没有任何豁免**：符合该形状的四段版本号同样会被拦截；版本信息只能出现在登记字段中（`toolchain_id`、`major`、`exe_sha256`） |
| `TX-IPV6` | IPv6 全写和缩写（含 `::`、内嵌 IPv4） |
| `TX-MAC` | MAC 地址 |
| `TX-WINPATH` | 盘符用户目录、UNC 路径 |
| `TX-POSIXPATH` | `/home/`、`/Users/`、`/mnt/<盘符>/`、`/root/`。允许清单：`/home/agent/`、`/home/code/`、`/srv/`、`/opt/render-gates/`、`/etc/render/` |
| `TX-ENVDUMP` | 连续多行 `NAME=value` |
| `TX-SECRET` | `sk-ant-`、`tskey-`、私钥头、常见令牌前缀 |
| `TX-TAILNET` | **只拦截原始诊断的形状**：MagicDNS 完整域名（`<名称>.<tailnet>.ts.net`），以及带 `nodekey:`、`discokey:`、`mkey:` 前缀的密钥串。“DERP”“tailscale status”等术语本身**不拦截**，便于讨论设计 |
| `TX-TZ` | 带非 `Z` 时区偏移的时间戳 |

- 命中后只报告规则编号和固定引用。引用（`ref`）只能是固定对象名（如 `result.json`、`bundle`）、artifact_id，或**只由 schema 中已定义属性名组成**的 JSON Pointer。未知键报 `schema_invalid`，引用为文件级，**不把原始键名拼进引用**。属性名清单由 `tools/update_ref_pattern.py` 从 schema 生成，测试 SC03 检查两者是否同步。
- 本机侧先执行同样的检查。规则集带版本号。

## 9. 限额与常量

| 对象 | 上限 |
|---|---|
| submission / job.json | 64 KB |
| bundle.tar | 50 MB；≤ 2000 个条目；解包后 ≤ 200 MB；路径 ≤ 200 字符；深度 ≤ 16 |
| heartbeat / status / result.json | 4 KB / 16 KB / 256 KB |
| 各产物上传上限 | log 256 KB；keyframe 和 contact 各 8 MB（关键帧最多 16 张）；preview 20 MB；final 200 MB |
| 单个尝试上传总量 | `limits.max_upload_mb`（≤ 200 MB），只统计 `delivery = uploaded` 的产物 |
| `up/` 文件系统 | 4 GiB |

### 9.1 重试与时序常量（两端配置必须一致）

| 常量 | 值 | 性质 |
|---|---|---|
| Worker 轮询任务 | 5–10 秒 | 初值，联调验证 |
| ingest 轮询 | 5 秒 | 初值 |
| 心跳间隔 / 租约 | 15 秒 / 90 秒 | 初值 |
| 运行中状态的最长上报间隔 | 30 秒 | |
| `download_retry_limit` | 3 | **协议重试**：某个尝试下载或校验失败后重新下载的次数；用尽后上报 `validation_error` |
| `upload_retry_limit` | 3 | **协议重试**：同一尝试被判 `rejected` 后的重传次数；由 ingest 计数，用尽后判 `delivery_failed` |
| 传输层重连 | 5 秒起，翻倍，最多 300 秒，不限次数 | **传输重试**：连接错误，不计入上面两项 |
| `limits.max_attempts` | 1–3（由任务指定） | **执行重试**：见 6.9 |

## 10. 跨字段规则（XF）

参考实现：`refimpl/render_protocol/semantics.py`、`bundle.py`；测试：`tests/test_semantics.py`。“负责端”中：R = renderctl 预检，P = publish 强制，W = Worker 强制或自检，I = ingest 强制。

### 10.1 提交（XF-J），负责端 R / P / W

| 编号 | 规则 |
|---|---|
| J01 | `frame_end_exclusive > frame_start` |
| J02 | `stage = keyframes` 时必须有 `render.keyframes` |
| J03 | 每个关键帧都在 `[frame_start, frame_end_exclusive)` 内 |
| J04 | 阶段与配置：keyframes 只能是 {png}；preview 必须含 h264-preview，可含 png；final 必须含 h264-final，可含 png 和 h264-preview |
| J05 | 阶段与产物：keyframes 必须含 {log, keyframe}，可含 contact；preview 必须含 {log, preview}，可含 keyframe、contact；final 必须含 {log, final}，可含其余几种 |
| J06 | 配置集合等于所请求产物需要的配置集合：每个产物都有对应配置，且没有多余配置 |
| J07 | `final_delivery = upload` 只允许用于 `stage = final` |
| J08 | 请求 preview 时，`(end − start) ≤ 20 × fps` |
| J09 | `final_delivery = upload` 时，`(end − start) ≤ 600 × fps` |
| J10 | 请求 keyframe 产物时必须有 `render.keyframes` |
| J12 | `entrypoint` 是任务包中的一个普通文件（P、W） |

### 10.2 任务包路径（XF-B），负责端 P / W（两端规则相同）

| 编号 | 规则 |
|---|---|
| B01 | 不允许 `.` 或 `..` 段 |
| B02 | 每段只允许 `[A-Za-z0-9._-]`，长 1–64。这样排除了 `\ : * ? " < > |`、空格、`~`、`$` 和非 ASCII，也就排除了 NTFS 短名、ADS 和 Unicode 等价形式 |
| B03 | 段不能以 `.` 结尾（Windows 会去掉尾点，造成别名） |
| B04 | 段的第一个点之前的部分，不区分大小写，不能是 Windows 保留设备名：CON、PRN、AUX、NUL、COM0–9、LPT0–9 |
| B05 | 不能是绝对路径，不能有空段，不能以 `/` 结尾 |
| B06 | 路径 ≤ 200 字符，深度 ≤ 16 |
| B07 | 同一个统一的、不区分大小写的名称空间覆盖**所有显式条目和所有隐式父目录**，同一名称只能有一种拼写（例如 `Scene/a.js` 与 `scene/b.js` 冲突）。允许子文件先隐式创建目录、之后出现拼写完全相同的显式目录条目 |
| B08 | 同一名称（不区分大小写）不能既是文件又是目录 |
| B09 / B11 | 条目数 ≤ 2000；解包总量 ≤ 200 MB |
| B10 | 只允许普通文件和目录（拒绝符号链接、硬链接、设备、FIFO） |
| B12 | tar 文件损坏或无法解析 |
| B13 | 不允许完全相同的重复条目（否则后一个条目会覆盖前一个，不同解包工具的行为也可能不同） |

跨语言路径测试向量：`examples/protocol/vectors/bundle-path-vectors.json`。

### 10.3 结果（XF-R），负责端 I（Worker 上传前自检）

| 编号 | 规则 |
|---|---|
| R01 | `job_id` 与任务一致 |
| R02 / R03 | `bundle_sha256` 和 `adapter` 与 job.json 一致。“任务定义不可用”分支是唯一例外：`failed` + `validation_error` + `phase_reached = validate` + `rendered = 0`，此时 `bundle_sha256`、`adapter`、`frames.expected` **三者同时为 null** |
| R04 | `attempt_no ≤ limits.max_attempts` |
| R05 | 同一结果中 `artifact_id` 唯一 |
| R06 | `artifact_id` 的前缀等于 `kind` |
| R07 | kind、media_type、export_profile 对应：log 对应 text/plain，无配置；keyframe 和 contact 对应 png 与 png-rgb8-v1；preview 对应 mp4 与 h264-preview-v1；final 对应 mp4 与 h264-final-v1 |
| R08 | 产物的配置属于任务的 `export.profiles` |
| R09 | 产物的 kind 属于任务的 `artifacts` |
| R10 | keyframe 必须有 `frame_index`，它属于任务关键帧，且 `artifact_id` 为 `keyframe-<frame_index>`；其他 kind 不得有 `frame_index` |
| R11 | 上传的产物不超过各自的上限，总量不超过 `max_upload_mb` |
| R12 | 交付方式：非 final 产物必须上传；`final_delivery = retained_local` 时 final 必须本地保留；`final_delivery = upload` 时 final 必须上传，或者本地保留并标 `over_upload_cap: true` |
| R13 | `succeeded` 时：`rendered = expected`；任务请求的每一类产物都存在；关键帧集合等于任务关键帧 |
| R14 | `succeeded` 且含视频产物时，`encoder` 不能为 null |
| R15 | `frames.expected` 等于按任务计算的值（keyframes 阶段为关键帧数量，其他阶段为 `end − start`）；只有在“任务定义不可用”分支中才可以为 null，此时跳过比较 |
| R16 | `rendered ≤ expected` |
| R17 | `failed` 必须有错误分类且不是 cancelled；`cancelled` 的错误分类必须是 cancelled |
| R18 | `exit.kind = not_started` 时，`phase_reached` 只能是 validate 或 prepare，且 `rendered = 0` |
| R19 | `retry_scheduled = true` 时：`failed`，错误分类可重试（`adapter_error`、`worker_lost`），且 `attempt_no < max_attempts` |
| R20 | 各阶段耗时之和 ≤ `total` |
| R21 | `gpu_query ≠ available` 时，`gpu_busy_observed` 和 `vram_peak_mb` 必须为 null |
| R23 | 每个结果都必须包含一个已上传的 log |

schema 层的条件约束（`succeeded` 时 exit 必须是 exited 且 code 为 0、`software_fallback = false`、browser 和 toolchain_id 不能为 null 等）见 result-v1，由 schema 组测试覆盖。

### 10.4 状态与心跳（XF-S / XF-H），负责端 I / W

| 编号 | 规则 |
|---|---|
| S01 / S02 | `job_id` 一致；`attempt_no ≤ max_attempts` |
| S03 / S04 | `frames_done ≤ frames_total`；`frames_total` 等于按任务计算的帧数 |
| H01 | `active_job_id` 与 `active_attempt_id` 同为 null 或同不为 null；`busy` 时不为 null，`idle` 时为 null |
| H02 | `capabilities.adapters` 中的 id 唯一 |

违反 S 或 H 规则的消息被忽略，不改变任何状态，只计入 ingest 的管理员统计。

## 11. 消息索引

| 文件 | schema `$defs` | 写入方 | 读取方 |
|---|---|---|---|
| outbox/submissions/*.json | job-v1 `submission` | Claude | publish |
| outbox/control/*.cancel.json | job-v1 `cancel_submission` | Claude | publish |
| down/jobs/*/job.json | job-v1（根） | publish | Worker |
| down/control/*/cancel.json | job-v1 `cancel_request` | publish | Worker |
| inbox/submissions/<key>.json | job-v1 `submission_receipt` | publish | Claude |
| inbox/submissions/<job>.cancel.json | job-v1 `cancel_receipt` | publish | Claude |
| up/_worker/heartbeat.json | status-v1 `heartbeat` | Worker | ingest |
| up/jobs/*/*/status.json | status-v1 `status` | Worker | ingest |
| down/acks/_worker/heartbeat.json | status-v1 `heartbeat_ack` | ingest | Worker |
| inbox/workers/worker-local.json | status-v1 `worker_view` | ingest | Claude |
| inbox/jobs/*/status.json | status-v1 `job_view` | ingest | Claude |
| inbox/netcheck/status.json | status-v1 `netcheck_view` | netcheck | Claude |
| up/jobs/*/*/result.json | result-v1 `result` | Worker | ingest |
| down/acks/*/*.json 和 inbox/jobs/*/*/ingest.json | result-v1 `ingest_record` | ingest | Worker / Claude |
| inbox/handoff/*.record.json 和 down/acks/_handoff/*.json | result-v1 `handoff_record`（r5.1） | ingest | Claude / Worker |

`examples/protocol/` 中每种消息至少有一个样例，文件名为 `<$defs 名>[.<变体>].json`。

## 12. 错误分类

`validation_error`、`adapter_error`、`timeout`、`gpu_oom`、`disk_full`、`cancelled`、`worker_lost`、`upload_error`、`internal`。其中可重试（可设 `retry_scheduled`）的只有 `adapter_error` 和 `worker_lost`。`worker_lost` 在结果中的含义是“该尝试因 Worker 进程自身中断而终止”，与 VPS 显示的 `worker_lost` 叠加标记不同。

**退出码**：`exit.kind` 取值为 `not_started`、`exited`、`signaled`、`terminated_by_worker`、`unknown`。Windows 原生进程的退出码按 DWORD 原样上报（0–4294967295，例如 NTSTATUS 访问冲突对应 3221225477）；POSIX 信号用 `signaled` 加 `signal` 表示。

**观测字段**：未知时显式写 null，**null 不等于 false**。`device.gpu_query` 说明设备查询本身是否可用。

## 13. 版本

`protocol_version: 1`。字段变更必须先改本文档和 schema，然后重新生成样例和属性名清单，并跑完三组测试。

## 14. 与 HTTP v2 的映射

| 目录协议 | HTTP v2 |
|---|---|
| 列举 `down/jobs/` | `POST /v2/leases`（原子领取） |
| `cancel_request` | 领取或心跳响应中的 `cancel` 字段 |
| heartbeat / `heartbeat_ack` | `POST /v2/workers/{id}/heartbeat` 及其响应 |
| status | `POST /v2/attempts/{id}/status` |
| 产物 + result | `PUT /v2/attempts/{id}/artifacts/{artifact_id}` + `POST /v2/attempts/{id}/complete` |
| `ingest_record` | `complete` 的响应 |

JSON 文档与 v1 一致。

## 15. 测试分层

| 层 | 位置 | 依赖 | 覆盖 |
|---|---|---|---|
| schema | `tests/test_schemas.py`（SC） | jsonschema ≥ 4.18；没有时整组报告为跳过 | 样例合法；元模式；属性名清单同步；反例被拒 |
| 跨字段语义 | `tests/test_semantics.py`（SE） | 标准库 | R1 身份与测试向量；XF-J、XF-B、XF-R、XF-S、XF-H |
| 状态机 | `tests/test_state_machine.py`（ST） | 标准库 | 5.1–5.5 |
| 真实部署验收 | `docs/acceptance-v1.md` | 部署后 | SB、GT、FS、HB、PR、MX、TX、GPU、PV |

前三层通过只说明协议文档和参考实现自洽，**不代表**部署验收通过。

## 16. 设计决定的实施约定（双方已确认）

| 决定 | 实施约定 |
|---|---|
| 状态允许向前跳跃 | 仍然检查合法枚举、seq、进度单调，以及结果与已上报终态一致（`outcome_mismatch`）；不能跨终态改写 |
| `retry_scheduled` | 保留错误类别（`adapter_error`、`worker_lost`）和尝试上限约束；`retry_pending` 期间取消，会取消剩余全部重试并收敛到终态（5.4） |
| 隐私隔离即终态 | 不自动重试；修正后作为新任务提交，用 `parent_job_id` 保留追溯关系 |
| 禁止浮点 JSON 数值 | 小数参数以字符串传递，由场景或适配器解析，并明确处理范围和非有限值。小数字符串的不同写法（如 `"1.5"` 和 `"1.50"`）视为不同输入，不做额外规范化 |
| 结果中的 `worker_lost` | 指 Worker 进程自身中断导致的执行中断。renderctl 和文档把“执行中断”与“网络失联”（显示标记）分开表述；**网络断开不得触发执行重试** |
| 重启不接管残留渲染 | 只终止**可以验证属于该尝试**的进程树（例如 Windows Job Object，或者记录的进程标识加创建时间），不能只凭可能被复用的 PID；确认已停止后才重试。已完成且结果已落盘的尝试，恢复交付，不重新渲染 |

## 17. 测试运行说明

- 所有 JSON 和 Markdown 文件的读写都显式使用 UTF-8（写入时使用 `\n` 换行），不依赖宿主的默认编码。VPS 上用 `-X warn_default_encoding -W error::EncodingWarning` 运行时没有任何告警。
- 运行：`python3 -I -B tests/run_all.py`；Windows 上为 `py -3 -X utf8 -I -B tests\run_all.py`（`-X utf8` 是额外保险，r5 已不依赖它）。
- `tools/` 中的生成脚本需要 Python ≥ 3.10；测试本身只需 Python 标准库，schema 组另需 jsonschema ≥ 4.18。

