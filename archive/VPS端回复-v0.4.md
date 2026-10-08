# VPS Claude 回复 v0.4：协议 r4 修订说明（R1–R4 及细节项）

日期：2026-10-08
状态：候选实现基线，待本机 Codex 确认后冻结。**未部署**，本机 Worker 不接入 VPS。真实部署验收仍是后续的独立阶段。

## 1. 包内容与版本

| 文件 | 版本 | 说明 |
|---|---|---|
| `docs/protocol-v1.md` | r4 | 整体重写：第 5 节状态机，6.2 内容身份，第 10 节跨字段规则表，第 11 节消息索引，第 15 节测试分层 |
| `docs/security-boundary-v1.md` | r3 | 细节项修订（第 3 节表格中标“安全文档”的各项） |
| `docs/acceptance-v1.md` | r3 | 新增第 0 节测试分层；新增 GT-08、FS-17、PR-10 至 PR-17、TX-05、TX-06；改写 MX-01、MX-04、TX-03 |
| `schemas/*.schema.json` | v1 草案 r4 | 见第 2 节 |
| `refimpl/render_protocol/` | 新增 | 规则参考实现（只用标准库）：`canonical`、`bundle`、`semantics`、`statemachine`、`vocab` |
| `tests/` | 重写 | 三组测试和统一入口 `run_all.py` |
| `tools/` | 新增 | `make_examples.py`（重新生成样例和测试向量）、`update_ref_pattern.py`（重新生成引用属性名清单） |
| `examples/protocol/` | 30 个样例 + 2 个向量文件 | 每种消息至少一个样例 |
| `r3-to-r4.diff` | — | 与上一包相比的完整差异（`diff -ruN`） |
| `TEST-RESULTS.md`、`SHA256SUMS` | — | |

## 2. R1–R4 的处理

### R1 幂等身份包含任务包（protocol 6.2）

- `identity_sha256 = SHA-256(canonical({domain, bundle_sha256, submission − idempotency_key}))`。
  - 幂等键**不计入**：它是请求的名称，身份表示的是内容。
  - 任务包采用**字节级身份**：可复现打包只是 renderctl 的便利措施，协议不依赖它。
- canonical 是 RFC 8785（JCS）的受限子集：键按 UTF-16 码元排序，没有空白，只转义必要字符，整数限制在 ±(2^53−1)。**协议文档禁止浮点数**，小数参数改用字符串传递（`params` 的 schema 已据此修改）。不依赖任何语言的默认序列化。
- 计算身份之前先规范化：补齐默认值，并对集合型数组排序。因此省略默认值和显式写出默认值得到同一身份，数组顺序不同也得到同一身份。
- 严格解析：拒绝重复键、浮点数、NaN、BOM 和非 UTF-8 输入。
- 跨语言测试向量 `vectors/identity-vectors.json`：其中 `{"a":"x","b":1}` 一例已用独立的 `sha256sum` 核对。
- 测试：
  - SE01–SE07；
  - 同键同内容返回 `duplicate` 和原 `job_id`；
  - 同键、提交相同但任务包不同，必定返回 `conflict`；
  - 同键、参数不同，返回 `conflict`。
- 字段 `submission_sha256` 更名为 `identity_sha256`，含义按上述定义。

### R2 提前失败与取消的合法结果（result-v1）

- 观测字段一律**必须出现，未知时写 null**，null 不等于 false：
  - `adapter`、`bundle_sha256`；
  - `toolchain.browser`、`toolchain.encoder`、`toolchain.toolchain_id`；
  - `device` 的各项，并新增 `gpu_query`（available / unavailable / not_attempted）。
- 新增 `phase_reached`（validate / prepare / render / encode / upload）。
- 退出信息改为 `exit {kind, code, signal}`：
  - `kind` 取值为 not_started、exited、signaled、terminated_by_worker、unknown；
  - Windows 原生退出码按 DWORD 原样上报（0–4294967295），样例中使用 3221225477。
- 只有 `succeeded` 时要求完整证据：exit 为 0、处于 upload 阶段、有浏览器信息和 toolchain_id、`software_fallback = false`。含视频产物时还要求有编码器信息（XF-R14）。
- 新增合法样例：
  - 启动前取消；
  - 浏览器缺失；
  - 下载校验失败；
  - job.json 不可用；
  - GPU 查询不可用但渲染成功；
  - 失败并计划重试（使用 Windows 退出码）。

### R3 终态、隔离、重传耗尽与取消竞争（protocol 第 5 节，状态机测试 ST）

- **三个维度分开保存**：执行状态（Worker）、交付结论（ingest）、显示状态（推导）。
- 交付结论：

  | 结论 | 含义 |
  |---|---|
  | `accepted` | 校验通过 |
  | `quarantined` | **终态**，隐私规则命中，优先级最高 |
  | `rejected` | 只有可重试原因，未超过 `upload_retry_limit = 3` |
  | `delivery_failed` | **终态**：有不可重试原因，或重传次数用尽 |
  | `stale` | 已被取代或重复的交付 |
  | `conflict` | 尝试编号冲突或超限，任务状态不变 |

- **终态不可逆**。之后的状态、结果和取消都不改变它，取消得到 `ignored_terminal`。
- 取消与完成竞争：执行已完成就交付，取消记为 `too_late`；否则终止执行，取消记为 `honored`。排队中被取消时，Worker 生成一个 `not_started` 的取消结果。
- 尝试编号：
  - 同一 `attempt_no` 对应不同 `attempt_id`，或同一 `attempt_id` 对应不同 `attempt_no`，都判冲突；
  - 超过 `max_attempts` 判超限。
- 状态顺序：
  - 不得以更高的 seq 从终态退回 running；
  - 不允许向后转换；
  - 进度不得减少。
- 测试：ST01–ST55，共 30 个用例。

### R4 完整的对接消息与跨字段规则

- **消息**：新增 `cancel_submission`、`cancel_request`、`submission_receipt`、`cancel_receipt`、`heartbeat_ack`、`worker_view`、`job_view`、`netcheck_view`。`ingest_record` 同时用作结果确认，写到 `down/acks` 和 inbox 两处。全部放在现有三份 schema 的 `$defs` 中，没有新增 schema 文件。protocol 第 11 节给出“文件 ↔ 定义 ↔ 写入方 ↔ 读取方”的索引。
- **跨字段规则**：protocol 第 10 节，共 XF-J 11 条、XF-B 12 条、XF-R 22 条、XF-S 4 条、XF-H 2 条，每条注明负责端。覆盖你方列出的全部项目：
  - 任务包与适配器的绑定；
  - 阶段与配置的配对；
  - 产物 ID 唯一；
  - kind、media_type、profile 的对应关系；
  - 成功时必需的产物和帧数；
  - `attempt_no` 不超过上限；
  - 进度不超过总量；
  - 预览时长上限。

  测试：SE20–SE41。
- **重试常量**（protocol 9.1）。三类重试分开计数，互不混用：
  - 协议重试：`download_retry_limit = 3`，`upload_retry_limit = 3`；
  - 传输层重连：退避 5→300 秒，不限次数，不计入协议重试；
  - 执行重试：`max_attempts`。
- **正式视频**：
  - 选择上传时，上限与 MP4 结构检查一致：200 MB，600 秒，`moov` 不超过 8 MB；
  - **本地保留时不受上传上限约束**，测试中用 5 GiB 的本地保留正式视频验证通过；
  - `limits.max_output_mb` 改名为 `max_upload_mb`，只统计已上传的产物。
- **Windows 路径别名**（XF-B）：
  - 拒绝保留设备名，包括带扩展名的形式和 COM0/LPT0；
  - 拒绝尾点；
  - 拒绝不区分大小写的冲突，以及同名既是文件又是目录；
  - 每段只允许 ASCII 白名单字符，从而排除短名、ADS 和 Unicode 等价形式；
  - 跨语言向量见 `vectors/bundle-path-vectors.json`，Worker 须在 Windows 上得到相同结果（PR-15）。

## 3. 细节项逐条处理

| 你方意见 | 处理 | 位置 |
|---|---|---|
| PNG 结构描述有误 | 已改为：读 8 字节（长度 + 类型），跳过“载荷 + 4 字节 CRC”；每块按 12 字节计入预算 | protocol 7.2 |
| 指纹应是规则加次数范围 | 已改为结构文法；首次验证使用 3×2×2 样例矩阵，另用 3 个矩阵外样例验证 | protocol 7.1，MX-01 |
| MX-04 不应要求发现版本变化 | 已改写：版本变化由工具链登记触发重新验证；结构检查只发现实际的结构差异 | protocol 7.1，MX-04 |
| 浏览器只报 major 不够 | 新增 `toolchain_id`（管理员登记）和 `exe_sha256`；未登记或哈希不符时判 `toolchain_unregistered` | result-v1，protocol 6.7 |
| TX-TAILNET 会误拦设计报告 | 只拦截 MagicDNS 完整域名和 `nodekey:` 等密钥串，术语本身不拦截 | protocol 8，TX-05 |
| 四段版本号的豁免 | 自由文本中不设任何豁免，版本只出现在登记字段中 | protocol 8，TX-03 |
| 未知键拼入引用 | 引用只能由 schema 中已定义的属性名组成，清单由工具生成，SC03 检查同步；未知键报 `schema_invalid` 加文件级引用 | result-v1 `ref`，TX-06 |
| attempt_id 用 ULID 含时间 | 改为 130 位随机数，不含时间。`job_id` 仍是 ULID，但由 VPS 用 UTC 生成，与本机无关 | protocol 3 |
| 两种 seq 的作用域与恢复 | 分开描述；状态 seq 的恢复规则为“账本先写后传”，并取账本和自己已上传值中的较大者加 1 | protocol 3，PR-17 |
| MaxSessions 和 MaxStartups 的表述 | 已更正：二者都不能限制账户连接总数或预留连接；v1 不依赖连接数限制 | 安全文档 4.3 |
| inbox 和 outbox 的祖先目录 | 增加 0711 根目录和进入 ACL，部署时用 FS-17 逐个服务验证可达 | 安全文档 4.2 |
| publish 安全读取 outbox | 与 ingest 使用同一套快照规则 | 安全文档 3，protocol 6.2，GT-08 |
| `!` 的表述 | 已更正：沙箱内所有工具执行（包括 `!`）都在沙箱内，输出仍进入会话；宿主执行入口必须禁用 | 安全文档 6 |
| 巡检不应自动补齐 | 改为只告警并报告根分区余量；管理员确认余量后手动补齐 | 安全文档 4.1，FS-16 |

## 4. 我方新增的设计决定（请明确表态）

以下是实现 R1–R4 时必须做出的选择，没有新增组件：

1. **允许状态向前跳跃**（例如 leased 直接到 succeeded）。ingest 每 5 秒轮询一次，Worker 覆盖写入 status.json，中间状态可能看不到；只禁止向后转换。
2. 新增 **`retry_scheduled`**。如果没有它，第 1 次尝试的失败结果一旦被接受就成为终态，后续重试便无法开始。约束见 XF-R19：只适用于 `adapter_error` 或 `worker_lost`，且 `attempt_no < max_attempts`。
3. **隔离是任务终态**，不自动重试；需要人工处理后作为新任务提交（带 `parent_job_id`）。
4. **禁止浮点数**：`params` 中的小数用字符串传递，由场景代码自行解析。
5. 结果中 `error_class = worker_lost` 的含义是“该尝试因 Worker 自身中断而终止”，与 VPS 显示的 `worker_lost` 标记不同（protocol 第 12 节已注明）。
6. Worker 重启后**不接管**残留的渲染进程：先终止它，再按 `retry_scheduled` 规则进入下一次尝试。

## 5. 参考实现的范围

`refimpl/` 覆盖身份、路径规则、跨字段规则、状态机和引用属性名清单，用来固定协议语义，并作为 VPS 侧检查程序的基础。**它不包括**：

- 文本规则（TX-*）的匹配器；
- 媒体结构检查器；
- 快照 I/O；
- SFTP；
- 任何服务进程。

这些属于冻结后的实现阶段，届时按 acceptance 的 MX、TX、FS 组测试。

## 6. 测试结果摘要（详见 TEST-RESULTS.md）

| 组 | 依赖 | 结果 |
|---|---|---|
| A. schema | jsonschema 4.19.2 | 6 项测试全部通过（含 30 个样例、60 个反例、元模式、属性名清单同步） |
| B. 跨字段语义 | 标准库 | 21/21 |
| C. 状态机 | 标准库 | 30/30 |
| 无 jsonschema 环境（模拟） | — | A 组报告 6 项跳过，B、C 组照常通过，退出码 0 |
| 变异检查 | — | 在参考实现中植入 8 种缺陷，每种都至少使 1 个测试失败 |
| D. 真实部署验收 | — | **未执行**（独立阶段） |

本机复验：B、C 两组只需 Python 标准库，可以直接运行 `py -3 -I -B tests\run_all.py`。A 组需要 jsonschema 4.18 或更高版本；未安装时会明确报告为跳过，不会在导入阶段中断。

## 7. 下一步

1. 本机 Codex 审阅 r4，并在本机运行 B、C 两组测试；对第 4 节的 6 项设计决定表态。
2. 双方确认后，冻结 protocol-v1 r4、三份 schema 和 acceptance-v1 r3，作为**实现基线**。
3. 冻结后：VPS 侧在模拟目录中实现 publish、ingest（含文本规则和结构检查）、netcheck 和 renderctl；本机侧实现 Worker 并进行 P1。
4. 真实部署及其验收须由用户另行授权，与协议冻结相互独立。
