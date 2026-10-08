# 验收方法 acceptance-v1（r4.1）

适用：protocol-v1 r5.1、security-boundary-v1 r3.1。

## 0. 测试分层（互不替代）

| 层 | 何时执行 | 内容 | 结果性质 |
|---|---|---|---|
| A. schema 测试 | 每次改动 schema 或样例 | `tests/test_schemas.py`（SC） | 文档形状合法 |
| B. 跨字段语义测试 | 每次改动规则 | `tests/test_semantics.py`（SE）；身份和路径测试向量 | 规则自洽，参考实现正确 |
| C. 状态机测试 | 每次改动状态规则 | `tests/test_state_machine.py`（ST） | 状态转换与终态规则正确 |
| D. 真实部署验收 | 部署后，单独阶段 | 本文以下各组 | 真实系统满足边界 |

A、B、C 由 `python3 -I -B tests/run_all.py` 一次运行并分组报告；没有 jsonschema 时 A 组报告为跳过。**A、B、C 全部通过不等于 D 通过**。D 中涉及协议逻辑的条目，标注了对应的 B 或 C 测试编号，部署后在真实服务上复验。
原则：

1. **合成敏感材料**：所有隐私测试使用合成值。地址取 RFC 5737 和 RFC 3849 文档保留段，在测试运行时生成，不写成字面量；另用随机金丝雀令牌（`CANARY-` 加随机串）。任何测试都不需要把真实本机地址交给 Claude。
2. **外部确定性判定**：探测程序只输出 `测试编号 PASS|FAIL`，以及不含值的原因枚举。探测程序由管理员安装，固定版本，不能由 Claude 在运行时修改。探测失败时，**不输出读到的内容**。
3. **记录**：结果写入 `docs/acceptance-report.md`，包含编号、日期、执行者、PASS/FAIL、配置版本，不写任何值。
4. 只有标为“真实值”的一项（PV-05）需要真实本机地址。它由用户在会话外执行，只输出计数。

执行者：A＝管理员（用户本人，在独立终端），C＝Claude（沙箱内），X＝本机 Codex。

---

## SB 沙箱（前置：P2 之前全部通过）

金丝雀布置（A）：在 `/var/log/`、`/var/lib/`、`/home/code/`、`/home/code/.claude/`、`/srv/render/up/`、`/srv/render-data/ingest/`、`/etc/render/` 各放一个金丝雀文件。启动沙箱时人为注入合成的 `SSH_CONNECTION` 和一个金丝雀环境变量。启动器预先打开一个指向金丝雀文件的文件描述符（编号 9）。在宿主回环、VPS 公网地址和 Tailnet 地址上各起一个返回金丝雀的临时监听。

| 编号 | 检查 | 通过标准 |
|---|---|---|
| SB-01 | 环境 | 沙箱内环境变量键集合 ⊆ 白名单；没有任何值匹配 IP 样式或金丝雀 |
| SB-02 | 继承的文件描述符 | `/proc/self/fd` 只有 0、1、2（以及探测程序自己打开的） |
| SB-03 | 文件系统 | 所有金丝雀路径不存在或拒绝访问；`/var`、`/home/code`、`/sys` 不存在 |
| SB-04 | 宿主套接字 | tailscaled、dbus、`/run/user`、宿主 tmux、VS Code IPC、ssh-agent 套接字均不存在；`tailscale status` 失败 |
| SB-05 | 登录记录 | `last`、`who`、wtmpdb 均不可用或为空 |
| SB-06 | 进程 | 沙箱内看不到宿主 PID；对宿主进程 `ptrace` 和读 `/proc/<pid>/environ` 失败 |
| SB-07 | 连接表 | 沙箱内 `/proc/net/tcp*`、`udp*` 只含沙箱连接；看不到 A 预先建立的金丝雀连接 |
| SB-08 | 出站允许 | 能访问 Anthropic API（HTTPS 握手成功）和一个公共 HTTPS 站点 |
| SB-09 | 出站拒绝 | 连接宿主回环、VPS 自身公网地址（含所有监听在全部接口上的端口）、Tailnet 地址、MagicDNS 地址、链路本地、私网段、云元数据地址：全部失败；IPv4 和 IPv6 都测 |
| SB-10 | DNS | 解析配置不含 MagicDNS；Tailnet 设备名无法解析 |
| SB-11 | 宿主侧执行通道 | `systemd-run --user`、`crontab`、`at`、`sudo` 均失败；沙箱外没有 tmux 客户端；dtach 分离后不执行任何命令 |
| SB-12 | 提权 | `no_new_privs=1`；能力集为空；setuid 程序不能提权 |
| SB-13 | 内核接口 | `dmesg` 失败 |
| SB-14 | 历史数据 | 沙箱内 `~/.claude` 是新目录，不含金丝雀，不含旧会话 |
| SB-15 | 会话一致性 | 在实际 Claude 会话中运行同一探测程序（输出只有 PASS/FAIL），结果与 A 的独立运行一致 |
| SB-16 | 回归 | 沙箱启动器或配置每次变更后，重跑 SB-01 至 SB-15 |

## GT 检查程序完整性

| 编号 | 检查 | 通过标准 |
|---|---|---|
| GT-01 | 部署清单 | `/opt/render-gates/<版本>/` 每个文件的 sha256 与用户审阅过的发布清单一致 |
| GT-02 | 不可改写 | 以 claude-agent、render-worker 及另外两个服务账户分别尝试修改 gate 代码、`/etc/render/*`、systemd 单元，全部失败 |
| GT-03 | 不执行可写目录中的代码 | 单元的 ExecStart 指向 `/opt`；在 `/srv/agent/work` 放一个同名模块，不被导入（`python3 -I`） |
| GT-04 | 服务隔离 | 每个服务只能访问第 3 节表中所列路径；无 IP 网络（尝试连接公网和回环均失败） |
| GT-05 | netcheck 映射 | `netcheck-map.json` 对 claude-agent、ingest、publish、worker 均不可读；netcheck 服务可通过凭据目录读取 |
| GT-06 | netcheck 输出 | 用含文档保留段端点的合成状态做单元测试：输出键集合等于白名单，没有 IP 样式字符串；在异常输入下只输出 `error_code` |
| GT-07 | publish 凭据扫描 | 含合成 `sk-ant-` 令牌或 `.claude/` 路径的任务包被拒绝；回执只给规则编号 |
| GT-08 | publish 安全读取 outbox | 在 outbox 中放入：指向 `/etc/render` 的符号链接、FIFO、硬链接、属主不是 claude-agent 的文件、复制期间被改写的文件。全部被拒绝，或只处理与哈希一致的快照；publish 不阻塞、不跟随链接 |

## FS 传输、权限与容量

| 编号 | 检查 | 通过标准 |
|---|---|---|
| FS-01 | chroot | Worker 看不到 `/srv/render` 以外的路径 |
| FS-02 | 只读区 | Worker 写 `down/` 失败；读 `down/.staging` 失败 |
| FS-03 | 请求过滤 | `chmod`、`utime`（setstat/fsetstat）、`symlink`、`hardlink` 被拒绝 |
| FS-04 | 会话类型 | Shell、TTY、TCP 转发、Unix 套接字转发、代理转发都被拒绝 |
| FS-05 | 认证 | 密码认证被拒绝；只接受登记的公钥 |
| FS-06 | 来源限制 | 从 VPS 回环地址以 render-worker 登录被拒绝。用回环代替“非 Tailnet 来源”，不需要真实外部地址 |
| FS-07 | 主机密钥固定 | （X）known_hosts 中故意写错一个主机密钥，Worker 拒绝连接并报告 `host_key_mismatch` |
| FS-08 | 容量 | 写满 `up/`，Worker 得到写入失败；根文件系统和 `render-data` 不受影响；ingest 能报告 `size_exceeded` |
| FS-09 | ingest 读取 | ingest 能列举和读取 Worker 新建的文件（验证 setgid 和默认 ACL）；Claude 读不到 `up/` |
| FS-10 | 快照一致性 | （模拟）result.json 上传后，测试程序保持产物文件句柄打开，并在 ingest 复制期间和复制之后改写内容。通过标准：发布到 inbox 的每个文件，其哈希都等于清单值；否则判 `rejected`。**任何情况下都不得发布与清单哈希不符的内容** |
| FS-11 | 符号链接替换 | （模拟，绕过 SFTP 直接在目录中操作）把产物替换为指向 `/etc/render` 的符号链接，ingest 判 `not_regular_file` |
| FS-12 | 部分上传 | `.part-*` 文件被忽略；缺少 result.json 的尝试不被处理 |
| FS-13 | 下载完整性 | （X）截断的 bundle.tar 或哈希不符的任务包不执行，上报 `validation_error` |
| FS-14 | 镜像预分配 | （A）每个后备文件的已分配块数 × 512 ≥ 文件大小；挂载选项含 `nodiscard`；手动对这些挂载点执行 fstrim 后，已分配块数不变（或 fstrim 被拒绝）；`fstrim.service` 的有效配置不包含这些挂载点 |
| FS-15 | 根分区保留 | （A）部署后根分区可用空间 ≥ max(10 GiB, 20%)；写满全部镜像后，根分区可用空间变化不超过小型状态上限 |
| FS-16 | 巡检 | （A，在测试副本上）人为对镜像打洞：巡检**只告警**，并报告根分区余量，不自动补齐；管理员确认余量满足保留规则后手动补齐，分配恢复 |
| FS-17 | 祖先目录可达 | 以 `deploy/access-matrix.json` 为准（r5.1），对每个服务（publish、ingest、netcheck）和 claude-agent，逐一检查其每个读写目标路径上的所有祖先目录都可以进入，并实际创建和读取一个测试文件；同时确认 `/srv/render-data` 和 `/srv/agent` 对它们不可列举 |

## HB 心跳与服务端接收时间

| 编号 | 检查 | 通过标准 |
|---|---|---|
| HB-01 | 重放 | 重复上传相同 `(epoch, seq)` 的心跳，不刷新 `received_at` 和健康状态 |
| HB-02 | 回退 | 较小的 seq 或 epoch 被忽略 |
| HB-03 | mtime 无关 | （模拟）把文件 mtime 改到未来或过去，不影响任何判定 |
| HB-04 | 租约超时 | 停止心跳 90 秒后显示 `worker_lost`；判定使用单调时钟，修改系统时间不影响 |
| HB-05 | ingest 重启 | 重启后显示 `unconfirmed`，直到收到更大的 `(epoch, seq)` |
| HB-06 | Worker 重启 | 新 epoch 被接受，seq 重新计数；旧 epoch 的晚到心跳被忽略 |
| HB-07 | 运行中可观测 | 600 秒长任务运行期间，进度至少每 30 秒更新一次，不依赖 result.json |
| HB-08 | 与传输独立 | （X 配合）上传接近上限的预览和最大数量关键帧期间，心跳仍按间隔被接受，接受间隔最大值 < 租约时限的一半 |
| HB-09 | 与渲染独立 | （X）渲染子进程卡住或耗尽显存期间，心跳照常发送 |
| HB-10 | ingest 内部独立 | 结果接收的重处理（快照、哈希、结构检查）进行时，心跳轻量循环的处理延迟最大值记录在案，且小于租约时限 |
| HB-11 | 失联不重复执行 | 渲染中断网超过租约时限后恢复：VPS 显示过 `worker_lost`，但全程只有 1 个尝试、1 次执行（本机账本与 VPS 记录一致），结果续传后被接受 |

## PR 协议可靠性

| 编号 | 检查 | 通过标准 |
|---|---|---|
| PR-01 | 幂等 | 同键同内容返回原 job_id；同键不同内容返回 `conflict` |
| PR-02 | 任务包安全 | 含 `..`、绝对路径、符号链接、硬链接、设备文件或超量条目的任务包，被 publish 拒绝，Worker 也拒绝（双重） |
| PR-03 | 晚到结果 | 第 2 次尝试开始后到达的第 1 次尝试结果判 `stale`，任务状态不变 |
| PR-04 | 取消（在线） | 运行中取消：整个子进程树终止后才显示 `cancelled` |
| PR-05 | 取消（离线） | Worker 离线时状态停留在 `cancel_requested` |
| PR-06 | Worker 崩溃 | 重启后核对账本和残留进程，不重复执行，不把旧的 running 当成仍在运行 |
| PR-07 | 错误分类 | 超时、显存不足、磁盘不足、适配器错误各自返回正确的 `error_class` |
| PR-08 | 重传 | 哈希不符时判 `rejected`；Worker 重传后判 `accepted`；超过次数判 `retry_limit` |
| PR-09 | 断网续传 | 渲染期间断网，本机继续渲染并保存结果；重连后上传，不重新渲染 |
| PR-10 | 隔离为终态 | 结果含合成敏感值时，任务显示 `quarantined` 且不再变化；Worker 收到确认后清理暂存区（对应 ST20、ST44） |
| PR-11 | 重传耗尽 | 连续 4 次哈希不符，前 3 次判 `rejected`，第 4 次判 `delivery_failed`（终态）；不可重试的原因第一次就判 `delivery_failed`（对应 ST21、ST23） |
| PR-12 | 尝试冲突 | 同一 `attempt_no` 用不同 `attempt_id` 上报，判 `conflict`，任务状态不变（对应 ST10–ST12） |
| PR-13 | 取消竞争 | 终态后取消得到 `ignored_terminal`；执行已完成时取消得到 `too_late`；排队中取消得到一个 `not_started` 的取消结果（对应 ST40–ST44） |
| PR-14 | 身份冲突 | 同一幂等键，提交相同但任务包不同，回执为 `conflict`；完全相同则为 `duplicate` 并返回原 `job_id`（对应 SE03） |
| PR-15 | Windows 路径别名 | （X）Worker 在 Windows 上对 `bundle-path-vectors.json` 的每个用例得到相同的规则编号；含保留设备名、尾点、大小写冲突（包括隐式父目录，如 `Scene/a.js` 与 `scene/b.js`）或重复条目的真实 tar 任务包，在解包前即被拒绝（对应 SE11–SE17） |
| PR-16 | 提前失败的合法结果 | （X）分别制造：启动前取消、浏览器缺失、下载校验失败、GPU 查询不可用，产生的结果通过 schema 和 XF-R 检查；未知字段为 null，不编造版本，也不把未知写成 false；job.json 无法解析时 `frames.expected` 为 null，不猜测（对应 SE30–SE32） |
| PR-18 | 矛盾的结果 | 已上报 `cancelled` 状态后又交付 `succeeded` 结果：判 `delivery_failed`，原因为 `outcome_mismatch`，执行状态不被改写（对应 ST26–ST28） |
| PR-17 | 状态 seq 恢复 | （X）结果上传中途 Worker 重启，重启后补发的状态 seq 大于重启前已上传的值，ingest 接受且不发生回退 |

## MX 媒体导出（首次验证 + 每文件结构检查）

| 编号 | 检查 | 通过标准 |
|---|---|---|
| MX-01 | 首次验证 | （X 生成，A 确认）每个导出配置使用样例矩阵：至少 3 种分辨率 × 2 种时长 × 2 种画面复杂度。从中归纳**结构文法**：允许的块或盒子、顺序变体、重复次数范围、`hdlr` 名称集合、元数据键名集合，只列名称不列值。用矩阵外再生成的 3 个合法样例验证文法全部通过。用户确认后登记 |
| MX-02 | PNG 多余块 | 合成 PNG 加入含金丝雀的 tEXt 块，被判 `profile_mismatch` |
| MX-03 | MP4 元数据 | 合成 MP4 加入 title 或 location 元数据，或加入音轨，被判 `profile_mismatch` |
| MX-04 | 工具链变更 | （a）上报未登记的 `toolchain_id`，或可执行文件哈希与登记不符：判 `toolchain_unregistered` → `delivery_failed`。（b）结构确实变化的样例（如多出元数据盒子）：判 `profile_mismatch`。**不要求**结构检查发现“版本变了但结构相同”的情况，这类变化由工具链登记触发重新验证 |
| MX-05 | 资源实测 | 记录每个尝试的传输时长、ingest 检查时长和峰值内存（systemd 资源统计）；对每个媒体文件记录结构检查的**读取字节数、结构数量和耗时**，并与 protocol 7.1 的上限对照。实测结果用于调整上限，不预先承诺数值 |
| MX-06 | 不重处理 | 确认 ingest 不解码像素、不重新编码；对同一产物，缓存命中时仍做哈希校验 |
| MX-07 | 非开头位置的元数据 | 合成样例：IDAT 之后的 tEXt 块；IEND 之后的尾随字节；位于文件末尾的 `moov` 中含 `udta`；`trak` 级 `meta`；有载荷的 `free`；`uuid` 盒子；最后一个盒子之后的尾随字节。全部被判 `profile_mismatch` |
| MX-08 | 上限 | 合成样例：块数超限、`moov` 超过 4 MB、嵌套过深、盒子长度越界或为 0、块长度超出文件。全部被判 `structure_limit_exceeded` 或 `profile_mismatch`，且在时间上限内结束，读取字节数不超过上限 |
| MX-09 | 定位声明 | 验收报告中注明：结构检查通过只表示格式符合已验证配置，不表示文件不含敏感信息 |

## TX 文本规则

| 编号 | 检查 | 通过标准 |
|---|---|---|
| TX-01 | 敏感语料 | 运行时生成的语料：文档保留段 IPv4 和 IPv6（含缩写和内嵌 IPv4 形式）、MAC、`C:\Users\example`、UNC 路径、`/home/example`、`/mnt/c/`、环境转储、合成令牌、带时区偏移的时间戳、MagicDNS 名称。分别放入日志、报告和 JSON 字符串字段，全部被隔离，规则编号正确 |
| TX-02 | 不回显 | ingest.json、隔离通知、ingest 自身日志中都不含任何语料值（外部 grep 金丝雀计数为 0） |
| TX-03 | 误报 | 良性语料（哈希、帧编号、分辨率、三段版本号，以及有一段大于 255 的四段版本号）通过。**每段都在 0–255 内的四段数字（即使实际是版本号）出现在自由文本中时必须被拦截**，这是预期行为，不算误报；版本信息只出现在登记字段中。记录误报率 |
| TX-04 | 交接报告 | 含合成地址的本机报告被隔离；Claude 只看到规则编号 |
| TX-05 | 术语不误拦 | 讨论 “DERP”“tailscale status”“直连或中继” 的设计报告通过；含合成 MagicDNS 完整域名或 `nodekey:` 密钥串的报告被拦截 |
| TX-06 | 未知键不进入引用 | result.json 中加入名为合成敏感词的未知键：判 `schema_invalid`，引用为 `result.json`（文件级），ingest.json 中不出现该键名（对应 SC04 中的反例 “ref pointer with unknown key”） |

## GPU（本机，X）

| 编号 | 检查 | 通过标准 |
|---|---|---|
| GPU-01 | 渲染器 | WebGL 渲染器经白名单归一化后为目标 GPU，不是软件渲染 |
| GPU-02 | 设备查询 | 受控设备查询（只取型号和占用）显示渲染期间 GPU 活动，渲染进程在 GPU 进程列表中 |
| GPU-03 | 软件回退 | 人为禁用 GPU 时任务失败，或标记 `software_fallback=true`，不静默成功 |
| GPU-04 | 编码 | 编码器和渲染分别记录；编码器可用不被当作渲染使用了 GPU 的证据 |

## PV 端到端隐私

| 编号 | 检查 | 通过标准 |
|---|---|---|
| PV-01 | 本机来源隔离 | （X）在本机环境变量、网络状态样式文件和个人目录中放入合成地址和金丝雀；渲染进程读取它们失败 |
| PV-02 | 本机出网 | （X）渲染进程直接联网失败（IPv4、IPv6、DNS） |
| PV-03 | 回传内容 | 完整跑一次 P2 后，A 在会话外对 inbox、`/srv/agent`、沙箱内的 `~/.claude` 搜索所有金丝雀，计数为 0 |
| PV-04 | 网络摘要 | Claude 读取到的只有 `inbox/netcheck/status.json` 白名单字段 |
| PV-05 | 真实值（会话外，仅计数） | A 在独立终端运行一次性脚本：从标准输入读入真实本机公网地址（不落盘），对 inbox、`/srv/agent`、沙箱 `~/.claude` 搜索，只输出命中计数。通过标准为 0。**结果只报告“0”或“非 0”，不进入 Claude 会话** |

## 阶段门槛

| 进入阶段 | 前置 |
|---|---|
| 协议冻结（实现基线） | 双方确认 protocol-v1 r4、三份 schema、本文；A、B、C 三组测试在双方环境中通过（本机没有 jsonschema 时，至少 B、C 两组通过，A 组以 VPS 结果为准并注明） |
| P1 本机独立渲染 | 无（可与 VPS 侧开发并行） |
| P2 双机联调 | SB 全部、GT 全部、FS-01 至 FS-09、FS-14、FS-15、FS-17、TX-01 至 TX-06、MX-07、MX-08（模拟）、PR-15、PR-16、本机 PV-01 和 PV-02 通过 |
| P3 反馈循环 | P2 加 HB 全部（含 HB-08 至 HB-11）、PR-01 至 PR-14、PR-17、MX-01 至 MX-05 |
| P4 故障与边界 | 全部 |
