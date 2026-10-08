# VPS 端评审报告 v0.1（回应《VPS与本机渲染协作计划书》0.2）

日期：2026-10-08
编写方：VPS 上的 Claude Code（Opus 5.5）
收件人：用户 → 本机 Codex
性质：评审与只读核查结果。未部署任何服务，未修改网络、账户、防火墙、sshd 或 Tailscale 配置。

---

## 0. 结论摘要

1. **第一版建议采用“受限 SFTP 领取与回传”**：本机 Worker 经 Tailnet 主动连接 VPS 上仅限 chroot SFTP 的专用账户。VPS 是唯一权威文件系统，不采用双向或定向同步软件；HTTP 服务作为第二阶段升级路线，协议文档保持一一对应。
2. **隐私前提目前在操作系统层面未满足**，这是本轮最重要的发现。Claude 当前运行账户可读取 Tailscale 原始状态（其中含对端端点）和带公网来源地址的登录记录。本轮没有泄露，仅因为 Claude 刻意没有读取，属于“靠模型自律”，不是强制边界。**P2（本机 Worker 接入）之前必须先完成 Claude 进程隔离**，详见第 6 节。
3. 计划书需修改的主要设计：增加 VPS 侧“入站隔离区＋确定性脱敏闸门”，本机回传的结果和 Codex 报告都不得直接进入 Claude 可读目录；关键帧、预览、正式渲染拆成三个独立任务；以 VPS 本地原子目录重命名实现任务发布。详见第 4、5 节。

---

## 1. VPS 环境（第 12 节第 1 项）

| 项目 | 核查结果 |
|---|---|
| 操作系统 | Debian GNU/Linux 13 (trixie)，内核 6.12.111，x86_64，KVM 虚拟机 |
| CPU | 2 vCPU（QEMU Virtual CPU） |
| 内存 | 总 1.9 GiB，可用约 0.7 GiB；swap 4 GiB，已用约 0.35 GiB |
| 磁盘 | `/` ext4 58 GB，可用约 45 GB；`/tmp` 为 tmpfs，约 1 GB，**不得用于暂存大文件** |
| Python | 3.13.5，自带 sqlite3 库 3.46.1（无 sqlite3 命令行，不影响）；无 Pillow |
| Node | v20.19.2，**未安装 npm** |
| 其他 | git 2.47.3；ffmpeg 7.0.2 静态版（含 libx264/libx265/libvpx-vp9，可在 VPS 抽帧、去元数据）；tmux；OpenSSH sftp-server 已存在；**无 rsync** |
| Claude Code | 2.1.294 |
| 运行账户 | `code`（uid 1001），组：code、users、workspace；**无免密 sudo**；不在 adm/systemd-journal 组 |
| 运行中服务 | ssh、tailscaled、cron、qemu-guest-agent、systemd 常规单元、user@1001 |
| 监听端口 | TCP 22（所有接口）、**TCP 8765（所有接口，`node tools/serve.js`，已运行约 3 天）**、若干回环端口、两个仅绑定 Tailnet 地址的端口（推测为 tailscaled 自身，未核实）；UDP 41641（所有接口，Tailscale） |
| 主机防火墙 | 未发现 nft、iptables 或 ufw 工具；云厂商侧防火墙状态未知 |
| sshd 关键项 | `PermitRootLogin yes`、`PasswordAuthentication yes`，监听所有接口 |
| Tailscale | tailscaled 运行中，版本 1.102.4 |

结论：VPS 只适合调度、队列、少量抽帧和脱敏，不适合渲染。计划书中“本机离线时不回退到 VPS”的要求是正确的。

附带安全提示（与本项目相关，但修改须经用户批准）：root 可用密码登录且 sshd 对公网开放、8765 端口对所有接口开放且无主机防火墙。建议在新增 Worker 账户的同一次变更中一并处理。

---

## 2. Tailscale 与 worker-local 连通性（第 12 节第 2 项）

| 白名单项 | 结果 |
|---|---|
| tailscaled 是否运行 | 是 |
| 别名 worker-local 是否已知 | **未验证**（尚无别名映射，也无安全诊断入口） |
| worker-local 是否可达 | **未验证** |
| 直连或中继 | **未验证** |
| 当前 SSH 会话来源类别 | 经确定性脚本分类为“Tailnet 地址”（仅输出类别，未输出地址） |

本轮未运行 `tailscale status`、`tailscale ping`、`ss -tn`、`last`、`who`，也未读取 sshd 或系统日志内容。唯一一次调用 `tailscale status --json` 时把输出丢弃，只取退出码来证明权限，结果为 0，说明本账户**可以**读取原始状态。

安全诊断入口的建议实现：见第 6.3 节 `netcheck`。它以独立账户运行，只输出 `online / known / reachable / direct|relay|unknown` 四个枚举值。

---

## 3. 路线比较（第 12 节第 4 项，对应计划书 8.1）

| 维度 | 受限 SFTP 领取与回传（推荐 v1） | 定向目录同步（Syncthing 类） | HTTP 任务服务（v2 升级） |
|---|---|---|---|
| VPS 新增组件 | 无新守护进程；sshd 加一个 `Match User` 段和一个账户 | 两端各一个同步守护进程 | 一个常驻服务（systemd）、认证、上传续传、模式校验 |
| 本机组件 | Worker 内含 SFTP 客户端（Windows 自带 OpenSSH 或 paramiko） | 同步守护进程＋Worker 监视目录 | Worker 内含 HTTP 客户端 |
| 权限模型 | chroot＋`ForceCommand internal-sftp`＋禁转发＋目录 ACL＋只允许 Tailnet 来源 | 依赖同步软件的文件夹共享设置，粒度粗；删除会传播 | 令牌＋服务内授权，粒度最细 |
| 完整性 / 原子性 | **VPS 是唯一文件系统**：任务以目录 rename 原子发布；结果先传临时名，再用 `posix-rename` 原子落地 | 跨机器没有原子性；有半同步、冲突副本、删除传播问题 | SQLite 事务，原子领取最强 |
| 恢复 | Worker 轮询、文件即状态、按哈希续传，简单可推理 | 依赖同步软件的重扫描，状态难以推理 | 租约、心跳、重传都由服务实现，最完整 |
| 取消延迟 | 取决于轮询间隔（建议 5–10 秒） | 取决于同步延迟 | 可长轮询，最快 |
| 多 Worker | 不支持（v1 本就限定单 Worker） | 不支持 | 支持 |
| **隐私面** | sshd 只记录 Tailnet 地址（前提是 Worker 连接 VPS 的 Tailnet 名称或地址）；Claude 账户读不到 sshd 日志（已核实可见行数为 0） | **最差**：全局发现、中继、遥测会把本机地址交给第三方，也可能绕开 Tailnet；同步日志含对端地址且常与 Claude 同账户 | 服务访问日志会记录对端地址，须关闭或只给服务账户读 |
| 实现量（VPS 侧） | 最小：renderctl＋发布器＋入站闸门 | 中 | 最大 |

**判断**：v1 选 SFTP。它解决了计划书担心的“跨机器重命名不是原子锁”问题，因为所有重命名都发生在 VPS 同一个 ext4 文件系统上。它也不引入新的出网组件。**不推荐定向同步**，主要原因是隐私面和半同步语义。出现以下任一需求时升级 HTTP：第二个 Worker、秒级取消、需要服务端强制模式校验。协议 JSON 文档设计成与将来的 HTTP 请求体相同，升级时只换传输层。

---

## 4. 对计划书的修改意见

### 4.1 必须修改

1. **入站隔离区与脱敏闸门（新增）**。计划书 9.1 只要求本机侧脱敏，这是单层防线。建议本机回传内容只能写入 VPS 的 `incoming/`，Claude 账户**不可读**。由 VPS 上独立账户运行的确定性程序 `ingest` 校验后，再发布到 Claude 可读的 `inbox/`：
   - JSON 校验模式，删除白名单外字段；
   - 对所有文本（日志、报告、错误堆栈、文件名）扫描 IPv4、IPv6、MAC、`C:\Users\…`、`/home/…`、`/mnt/c/…`、主机名样式和令牌样式字符串。**命中任意 IP 样式字符串即隔离**，不区分公私网段，只允许显式白名单（如版本号字段）；
   - PNG 用 ffmpeg 重新编码，丢弃全部元数据块；MP4 用 `-map_metadata -1 -fflags +bitexact` 重新封装，并在 VPS 抽帧供 Claude 查看；
   - 隔离时只向 Claude 报告“文件 X、字段 Y、规则 Z 命中”，**不回显命中值**。
   - `handoff/local/`（本机 Codex 的报告）**同样经过此闸门**。Codex 在本机可执行网络诊断，它的报告是最高风险通道。
2. **Claude 进程隔离是 P2 的前置条件**，见第 6 节。计划书 9.1 第 4 条的情形已经发生：当前 Claude 账户可读含本机地址的底层状态。
3. **关键帧、预览、正式渲染拆成三个独立任务**，各有独立幂等键（如 `…-keyframes`、`…-preview`、`…-final`），不要用一个任务的 `artifacts` 列表表达阶段。这样 Claude 看完关键帧再决定是否提交正式渲染，失败重试的粒度也更小。

### 4.2 建议修改

4. 任务发布：发布器在 `jobs/.staging/<job_id>/` 写完全部文件并校验，然后把整个目录 `rename` 到 `jobs/<job_id>/`。Worker 只会看到完整目录，不再需要 ready 标志，但仍须校验 `bundle_sha256`。
5. 状态与心跳：Worker 写 `incoming/results/<job_id>/<attempt_id>/status.json`，以临时名上传后原子替换，内含递增 `seq`。心跳写 `incoming/_worker/heartbeat.json`。**判断是否失联以 VPS 文件系统的 mtime（VPS 时钟）为准**，不比较两端时钟。上传时不得保留本机 mtime（不用 `-p`）。
6. 结果完成判定：`result.json` 最后上传，它列出每个产物的大小和 SHA-256。`ingest` 只在 `result.json` 存在且全部哈希匹配时处理该 attempt。
7. 取消：VPS 写 `control/<job_id>/cancel.json`。Worker 确认终止进程树后，在结果中写入 `cancelled`；Worker 离线时，状态停留在 `cancel_requested`。
8. 本机读写范围：`jobs/`、`control/`、`handoff/vps/` 只读；`incoming/` 只写。chroot 根目录由 root 拥有。
9. 配额：`incoming/` 总量上限（建议 2 GiB）；单个预览不超过 20 MB；日志尾部不超过 256 KB。完整视频默认留在本机，`result.json` 中只写 `retained_locally: true` 和一个相对键，不写绝对路径。

### 4.3 协议字段意见（第 5 节示例）

- `job_id` 由 VPS 生成（ULID），`created_at` 用 VPS 时钟；新增 `stage`（`keyframes|preview|final`）、`keyframes`（如 `[0, 75, 149]`）、`adapter_version`、`bundle_sha256`（替代含义不清的 `source_sha256`，或明确它指任务包）、`parent_job_id`（追溯 P3 的修改链）。
- `network_policy` 在 v1 只允许 `offline`，其他值一律拒绝。
- 结果中的 `device` 只允许“厂商＋型号”字符串（如 WebGL `UNMASKED_RENDERER` 经过白名单清洗后的值），不含 GPU UUID、主机名或驱动序列号。
- `error_class` 用枚举：`validation_error | adapter_error | timeout | gpu_oom | disk_full | cancelled | worker_lost | internal`。
- 结果中禁止出现任何主机名、用户名、绝对路径或环境变量转储；适配器的异常堆栈在本机先归一化（路径替换为 `<task>/…`）。

### 4.4 图像读取能力

**已验证**：本 VPS 会话可直接读取 PNG 并看到画面（已用 ffmpeg 生成的测试图验证）。Claude 不能直接看 MP4，预览视频由 VPS ffmpeg 抽帧后查看。注意：Claude 看到的任何图像都会上传至 Anthropic。因此只允许**由渲染帧生成的**联系表和关键帧，**禁止桌面截图、终端截图或任何系统界面截图**，因为任务栏和网络图标可能带出信息。

### 4.5 渲染适配器（供 Codex 决策）

`webgl-frames-v1` 建议约定：页面导出 `setup(params)` 和 `async renderFrame(i, t)`。适配器在 resolve 后读取画布像素，用 `t = i / fps`，不依赖实时播放。GPU 证据使用 WebGL 渲染器字符串，并与“软件渲染（SwiftShader/llvmpipe）即判失败”的规则配合。我的倾向是 WebGL 路线用 **Windows 原生独立 Worker 账户**，因为 WSL 下无头 Chromium 的 GPU 路径不稳定；但应以 Codex 的实测为准。

---

## 5. 建议的 VPS 路径与账户（第 12 节第 3 项）

> 以下是待批准的设计，**均未创建**。需要 root 的步骤由用户在实施阶段执行，Claude 只提供脚本和说明。

```text
/home/code/workspace/SPECIAL-Remote-Rendering/   源码主副本（Git 管理），Claude 读写
/srv/render/                         root:root 0755（chroot 根要求 root 拥有）
  jobs/        发布者写，Worker 只读        root:render-pub  2775 / Worker 经 ACL 只读
  control/     发布者写，Worker 只读        同上
  handoff/vps/ Claude 写，Worker 只读       同上
  incoming/    Worker 写，仅 ingest 可读     render-worker:render-ingest 2730（Claude 不可读）
    results/ handoff-local/ _worker/
/srv/render-inbox/                   ingest 写，Claude 只读
  results/ handoff-local/ quarantine-reports/
```

| 账户 | 用途 | 关键限制 |
|---|---|---|
| `render-worker` | 本机 Worker 的 SFTP 登录 | 仅密钥；`Match User render-worker` + `ChrootDirectory /srv/render` + `ForceCommand internal-sftp` + 禁所有转发与 TTY；**只允许 Tailnet 来源地址段**；shell 为 nologin |
| `render-ingest` | 运行 ingest 和 netcheck | 系统账户；由 systemd path/timer 触发；唯一能读 `incoming/` 的账户 |
| Claude 运行身份 | 写代码、跑 renderctl | 不可读 `incoming/`、tailscaled 套接字、登录记录；见第 6 节 |

HTTP 备选（v2）：Python 3.13 标准库加 SQLite 3.46.1 已可用，无需额外安装。服务只绑定 Tailnet 地址，由 `render-ingest` 类独立账户运行，访问日志不记录对端地址。

---

## 6. 隐私边界（第 12 节第 6 项）

### 6.1 防止 Anthropic 凭据进入本机

- Claude Code 和 Anthropic 凭据只存在于 VPS 的 Claude 账户主目录；`render-worker` 被 chroot 到 `/srv/render`，看不到任何主目录。
- 发布器打包任务时使用**显式包含清单**，不打包整个目录；打包后扫描 `sk-ant-`、`ANTHROPIC_`、`.claude/`、`.credentials` 等模式，命中即拒绝发布。
- `handoff/vps/` 中的报告同样过一遍凭据扫描。
- 本机不安装或登录 Claude（与计划书一致）。

### 6.2 当前已验证的暴露面（只检查了可读性和类别计数，未读取内容）

| 暴露面 | 现状 | 风险 |
|---|---|---|
| `tailscale status --json`（tailscaled 本地接口） | Claude 账户**可调用**（退出码 0） | **高**：输出含对端公网端点。本机一旦在 Tailnet 中与 VPS 通信，其公网地址就在 VPS 的 tailscaled 状态里 |
| 登录记录 `/var/lib/wtmpdb/wtmp.db`、`/var/log/wtmp` | Claude 账户**可读**；wtmpdb 中有 26 条来源为公网地址的登录记录（仅计数） | **高**：`last` 一条命令即可显示。这些记录是否含本机地址**未验证** |
| 环境变量 `SSH_CONNECTION`/`SSH_CLIENT` | 已设置，来源类别为 Tailnet 地址 | 低到中：目前不是公网地址，但一次 `env` 就会进入上下文；若将来改从公网 SSH，就会变为高风险 |
| sshd/系统日志 | `/var/log/auth.log` 不存在；Claude 账户可见的 ssh 单元日志为 0 行；btmp 不可读 | 已满足 |
| `~/.bash_history`、VS Code Server 日志（92 个） | 含公网样式 IPv4 的文件数为 0 | 已满足（仅限 IPv4 样式扫描） |
| `~/.claude/` | 788 个文件中有 2 个含公网样式 IPv4 字符串 | **未验证**：可能只是版本号或 VPS 自身地址，Claude 不应打开核对。见 6.4 |
| `ss -tn` 等连接表 | 未调用 | 中：若有从本机公网地址直连 VPS 的 TCP 连接（如绕开 Tailnet 的 SSH），会显示出来 |

**结论：边界目前没有被强制。** 本轮未把任何地址读入上下文，依靠的是 Claude 刻意不调用这些命令。任何一次疏忽、一个被注入的指令，或用户以 `!` 前缀在会话中运行诊断命令，都会导致泄露。

### 6.3 建议的强制措施（P2 前完成，需用户批准后执行）

1. **用沙箱启动 Claude Code**，二选一：
   - (a) bubblewrap：只挂载项目目录、`/srv/render-inbox`（只读）、`/srv/render/jobs` 等发布目录和 Claude 自身配置。用 tmpfs 覆盖 `/run/tailscale`、`/var/lib/wtmpdb`、`/var/log`、`/var/lib/tailscale`；`--clearenv` 后只保留白名单环境变量。不改动 Tailscale 或系统包，回滚最简单。
   - (b) 独立 `claude` 账户：tailscaled 套接字移到 `0750 root:tsadmin` 目录，wtmpdb 改为 `0640`（用 `dpkg-statoverride` 固化）。改动面更大。
   我推荐 (a)，并在其中验证 `tailscale status` 和 `last` 均失败。
2. **sshd**：为 `render-worker` 设置 `Match` 段，只允许 Tailnet 地址段来源。顺带建议关闭 root 登录和密码登录（独立变更，需用户确认，注意先确认密钥登录可用，避免锁死）。
3. **netcheck**：以 `render-ingest` 账户运行的确定性程序。它读取 tailscaled 状态，按 root-only 配置中的设备名映射到别名 `worker-local`，只输出四个枚举值到 `/srv/render-inbox/netcheck.json`。程序源码由 Claude 编写、用户审阅；运行结果 Claude 只读这个 JSON。
4. **ingest 闸门**：见 4.1 第 1 条。验收时使用合成值，如文档保留段地址和 `C:\Users\example`，检验拦截效果，不使用真实地址。
5. **操作纪律**：用户不要在 Claude 会话里用 `!` 前缀运行任何网络诊断，因为该前缀的输出会直接进入对话。这类诊断请在与 Claude 无关的独立终端中运行。

### 6.4 无法由 Claude 保证、需要用户在会话外处理的事项

- `~/.claude/` 中那 2 个文件和 wtmpdb 历史记录是否含本机地址，Claude 无法在不读取的前提下判断。建议用户在**独立终端**（不是 Claude 会话，也不用 `!`）中自行比对。若发现本机地址出现在 `~/.claude/` 的历史会话记录里，应视为**已经发生过**向 Anthropic 的暴露，后续措施只能防止再次发生。
- Claude Code 自身的遥测内容不在本轮核查范围内。已知它不读取 `SSH_CONNECTION` 的值，但无法由本会话证明，标记为未验证。沙箱的 `--clearenv` 可降低这一风险。

---

## 7. 分工与对接（第 12 节第 5 项）

### 7.1 VPS Claude 负责

| 文件 | 内容 |
|---|---|
| `docs/protocol-v1.md` | 起草；目录布局、状态机、文件语义，以及与 HTTP v2 的映射 |
| `schemas/job-v1.schema.json`、`schemas/result-v1.schema.json` | 起草，双方共同冻结 |
| `client/renderctl` | Python 标准库实现；提供 workers/submit/status/logs/artifacts/fetch/cancel；只读 `inbox/` |
| `server/publish.py` | 按包含清单打包、算哈希、扫描凭据，原子发布到 `jobs/` |
| `server/ingest.py` | 入站校验、脱敏、隔离、图像重新编码、抽帧 |
| `server/netcheck.py` | 白名单网络摘要 |
| `tests/` | 协议测试：用本地目录模拟 SFTP 两端，覆盖半上传、重复提交、晚到结果、取消、合成敏感值拦截 |
| `docs/deployment-vps.md` + `deploy/vps/*.sh` | 账户、chroot、sshd Match、沙箱启动脚本；**只写不执行**，由用户审阅后运行 |
| `examples/shader-demo/` | 确定性着色器动画（`renderFrame(i, t)` 约定） |

### 7.2 本机 Codex 负责

`worker/`（监督进程、SFTP 领取与上传、本机排他锁、持久执行账本、进程树终止）、`webgl-frames-v1` 适配器、本机侧第一道脱敏、`docs/deployment-worker.md`、Worker 账户与出网限制、P1 本机独立渲染验收。

### 7.3 需要本机 Codex 提供（全部须经过脱敏，**不得包含任何 IP、端点、主机名、地区或用户路径**）

1. 硬件摘要：GPU 型号、显存、可分配给 Worker 的 CPU/RAM/磁盘额度、驱动版本。
2. 执行环境选择：WSL2 或 Windows 原生，以及小样测试结论（渲染器字符串是否为目标 GPU）。
3. Worker 的 SSH **公钥**（ed25519），由用户转交即可。
4. 确认 Worker 将以 VPS 的 **Tailnet 名称或 Tailnet 地址**连接，不使用公网主机名。
5. 本机侧脱敏规则清单，以及对第 4.3 节字段的意见。
6. 本机设备在 Tailnet 中的名称**不要发给 Claude**，由用户直接写入 netcheck 的 root-only 映射配置。

---

## 8. 待用户决定

1. 是否同意把“Claude 沙箱化”（推荐 bubblewrap）列为 P2 前置条件。
2. 端口 8765 的 `node tools/serve.js` 是否仍需要，以及能否改为只绑定回环或 Tailnet 地址。
3. 是否在同一变更窗口加固 sshd（关闭 root 登录和密码登录）。
4. 谁执行 root 级部署脚本（建议用户本人在独立终端执行）。
5. 在会话外核对 6.4 所列历史数据。

## 9. 下一步

| 顺序 | 动作 | 负责 |
|---|---|---|
| 1 | 用户转交本报告；Codex 回复第 7.3 节信息，并对第 3、4 节表态 | 用户、Codex |
| 2 | 冻结 `protocol-v1.md` 和两份 schema | 双方 |
| 3 | Claude 编写 publish/ingest/renderctl/netcheck 及协议测试，用本地目录模拟跑通 | Claude |
| 4 | 并行：Codex 完成 P1 本机独立渲染（5 秒 150 帧，并给出 GPU 证据） | Codex |
| 5 | 用户审阅并执行 VPS 部署脚本：沙箱、账户、chroot、sshd Match | 用户 |
| 6 | 隐私验收前置：在沙箱内确认 `tailscale status`、`last` 失败，ingest 能拦截合成敏感值 | Claude、用户 |
| 7 | P2 双机联调 | 双方 |

*本报告中的所有核查结果均来自只读命令。涉及网络的检查只输出类别或计数，全文不含任何地址。*
