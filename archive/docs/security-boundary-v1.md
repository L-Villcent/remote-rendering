# 安全边界 security-boundary-v1（r3.1，含 protocol r5.1 补丁 D1）

状态：设计草案。**所有内容均未部署**；账户、文件系统、sshd、nftables、Tailscale 均未改动。
目标：本机公网 IP 不通过网络连接，也不通过任何内容进入 Anthropic；Anthropic 凭据不进入本机。
方法：在操作系统层面切断敏感信息来源，限制代码访问权限，控制回传内容。扫描只是辅助，不替代边界。

---

## 1. VPS 现状中与边界相关的事实（只读核查）

| 事实 | 影响 |
|---|---|
| 当前 Claude 以 `code` 账户运行，不在沙箱内 | 本机 Worker 接入前必须迁移 |
| `code` 可调用 tailscaled 本地接口（`tailscale status` 退出码为 0） | 对端端点可被读取 |
| 登录记录（wtmpdb）对所有用户可读 | 历史登录来源地址可被读取 |
| `kernel.yama.ptrace_scope = 0` | 同一 uid 的进程可互相读取内存和环境，**必须使用独立 uid** |
| `/etc/resolv.conf` 中有 MagicDNS 解析器（并有一个私网解析器） | 沙箱须使用自己的解析配置 |
| bubblewrap、passt（pasta）未安装；nftables、acl、e2fsprogs 已安装（在 `/usr/sbin`） | 实施时需安装 bubblewrap 和 passt |
| 主机防火墙规则状态 | **未验证**（读取规则需要 root，规则内容也可能含地址） |
| sshd 有一条 `Include`；有效配置 | **未验证**（`sshd -T` 需要 root） |
| `dmesg_restrict=1`、`protected_hardlinks=1`、`protected_symlinks=1`、landlock 可用 | 有利 |

## 2. Claude 沙箱

### 2.1 选型

单独使用 bubblewrap 不够，需组合三层：**独立账户 `claude-agent`** + **bubblewrap 的全部命名空间** + **pasta 用户态网络和按 uid 的 nftables 出站规则**。

为什么不能共享宿主网络命名空间：共享时 `/proc/net/*` 会显示宿主连接表（含 SSH 对端），抽象 Unix 套接字可达，Tailnet 对端和本机服务可直接连接，这些都无法用挂载隔离消除。

### 2.2 启动链（不留宿主侧执行通道）

```text
用户经 Tailnet SSH 登录 claude-agent
  └ sshd: ForceCommand /usr/local/lib/render/claude-attach（root 拥有）
       └ dtach -a /run/claude-sandbox/sock   只转发终端字节，没有“分离后执行命令”之类功能
                     ↑
systemd 系统服务 claude-sandbox.service（User=claude-agent）
  └ /usr/local/lib/render/claude-entry（root 拥有）
       ├ 清空环境（env -i），只保留白名单变量
       ├ 关闭除 0/1/2 外的所有文件描述符
       └ pasta（独立网络命名空间）
            └ bwrap（user/pid/ipc/uts/cgroup/mount 命名空间，--new-session，--die-with-parent，丢弃全部能力）
                 └ dtach -N … tmux（客户端和服务端都在沙箱内）
                      └ claude
```

- **不在沙箱外运行 tmux 客户端**。tmux 的 `detach-client -E` 会让沙箱内的服务端在沙箱外执行命令，属于宿主侧执行通道。
- `claude-agent` 不启用 linger，没有可用的 `systemd --user` 总线；`/etc/cron.allow` 和 `/etc/at.allow` 不包含它。
- `claude-agent` 不在 sudo 组，没有 sudoers 条目。
- sshd 中 `Match User claude-agent`：仅公钥；禁用所有转发、X11、隧道和代理转发；`PermitUserEnvironment no`；只允许 Tailnet 来源。

### 2.3 环境白名单

`HOME=/home/agent`、`PATH`（只含沙箱内路径）、`LANG`、`TERM`、`TZ=UTC`、`DISABLE_AUTOUPDATER=1`、`CLAUDE_CONFIG_DIR=/home/agent/.claude`。其他全部丢弃，包括 `SSH_*`、`VSCODE_*`、`TMUX`、`DBUS_*`、`XDG_RUNTIME_DIR` 等。

### 2.4 挂载清单（白名单，其余不存在）

| 沙箱内路径 | 来源 | 方式 |
|---|---|---|
| `/usr`（以及符号链接 `/bin`、`/lib`、`/lib64`、`/sbin`） | 宿主 | 只读 |
| `/etc/ssl`、`/etc/ca-certificates*`、`/etc/nsswitch.conf`、`/etc/localtime` | 宿主 | 只读，逐项挂载 |
| `/etc/passwd`、`/etc/group`、`/etc/hosts`、`/etc/resolv.conf` | `/etc/render/sandbox/` 下的专用文件 | 只读；resolv.conf 指向公共解析器，不含 MagicDNS |
| `/opt/claude-code/<版本>`、`/opt/ffmpeg/<版本>` | 宿主，root 拥有 | 只读；由管理员升级，沙箱内禁用自动更新 |
| `/home/agent` | `/srv/agent/home` | 读写；**全新目录**，不挂回 `code` 的旧 `~/.claude` 和历史记录；需在沙箱内重新登录 |
| `/work` | `/srv/agent/work` | 读写，项目源码 |
| `/outbox` | `/srv/agent/outbox` | 读写 |
| `/inbox` | `/srv/render-data/inbox` | 只读 |
| `/proc` | 新的 proc（PID 命名空间内） | 只显示沙箱进程 |
| `/dev` | bwrap 最小 dev | 不含宿主设备 |
| `/tmp`、`/run` | tmpfs（有大小限制） | 不含宿主套接字 |
| `/run/claude-sandbox`（仅 dtach 套接字） | 宿主 0700 目录 | 读写 |

**明确不存在**：`/var`（含日志、wtmpdb、tailscale 状态）、`/home/code`、`/srv/render`、`/srv/render-data/ingest`、`/run/tailscale`、`/run/dbus`、`/run/user`、`/sys`、宿主 `/tmp`。

可选的附加层：landlock 或 seccomp 过滤器（禁止 `ptrace`、`keyctl`、`bpf`、`perf_event_open` 等）。

### 2.5 网络

- pasta：不做入站端口映射；禁止经网关地址映射到宿主回环（`--no-map-gw`）；DNS 不转发到宿主解析器。
- nftables（`meta skuid claude-agent`，覆盖 pasta 代沙箱发出的连接）：
  - 允许：发往公网地址的 TCP 443 和 80；发往指定公共解析器的 DNS。
  - 拒绝：本机地址（`fib daddr type local`，包括 VPS 自己的公网地址，从而挡住所有监听在全部接口上的服务端口）、回环、Tailscale 的 CGNAT 段（RFC 6598）和 ULA 段、MagicDNS 地址、链路本地地址、各类私网段、云元数据地址。IPv4 和 IPv6 都要覆盖。
- 沙箱出站即 VPS 出站；Claude 的必要通信（Anthropic API、文档、git）保持可用。

### 2.6 迁移期约束

- 在沙箱验收（acceptance-v1 的 SB 组）通过前：当前 `code` 账户下的 Claude 只做设计和模拟目录代码，**本机 Worker 不得接入**。
- 迁移完成后不再以 `code` 账户运行 Claude。`code` 仍是用户本人的管理账户。
- 旧 `~/.claude` 不挂入沙箱。若需迁移设置，由用户审阅后逐项复制。

## 3. 检查程序（gate）的完整性

| 服务 | 账户 | 读 | 写 | 网络 |
|---|---|---|---|---|
| render-publish | `render-publish` | `/srv/agent/outbox`（只读） | `/srv/render/down`、`inbox/submissions`、私有状态 | 无（`PrivateNetwork=yes`、`IPAddressDeny=any`、`RestrictAddressFamilies=AF_UNIX`） |
| render-ingest | `render-ingest` | `/srv/render/up`（只读） | `/srv/render-data/ingest`、`inbox/{jobs,workers,handoff}`、`down/acks` | 无（同上） |
| render-netcheck | `render-netcheck` | tailscaled 本地接口（Unix 套接字）、经 `LoadCredential=` 传入的别名映射 | `inbox/netcheck` | 无 IP 网络 |

- 代码安装：开发源码在 Claude 可写的仓库中；上线版本由**用户审阅差异**后复制到 `/opt/render-gates/<版本>/`（root 拥有，文件 0644）。部署清单记录每个文件的 sha256。
- 运行方式：`/usr/bin/python3 -I -B`，只用标准库，没有第三方依赖；systemd 单元中设置 `ProtectSystem=strict`、`ProtectHome=yes`、`NoNewPrivileges=yes`、`PrivateTmp=yes`，并对 `/srv/agent` 设置 `NoExecPaths`。publish 以外的服务对其设置 `InaccessiblePaths`。资源限制：`MemoryMax=256M`、`CPUQuota=50%`、`IOSchedulingClass=idle`。
- 策略和配置放在 `/etc/render/`，root 拥有：规则集、导出配置登记表（结构文法）、限额与常量、工具链登记表 `toolchains.json`（`toolchain_id` → 完整版本和可执行文件哈希）。
- **publish 读取 outbox 与 ingest 读取上传使用同一套快照规则**：逐级打开目录，带 `O_DIRECTORY|O_NOFOLLOW`；文件用 `O_NOFOLLOW` 打开后再 `fstat`，要求是普通文件，属主为 claude-agent，链接数为 1，大小不超过上限；然后边复制到私有暂存区边计算哈希，之后只处理副本。outbox 由 Claude 写入，在此视为不可信输入，与 Worker 的上传同等对待。
- netcheck 别名映射：`/etc/render/netcheck-map.json`（root 0600），通过 systemd `LoadCredential=` 只交给 netcheck 服务。其他服务和 claude-agent 都读不到。
- netcheck 输出只有：`online`、`known`、`reachable`（布尔）、`path`（`direct|relay|unknown`）、`checked_at`、`error_code`（枚举）。程序内部任何异常都只映射为 `error_code`，不输出异常文本。
- 三个服务互相不能写对方的目录；ingest 被恶意上传攻破时也不能发布任务。

## 4. 目录、权限与容量

### 4.1 文件系统与容量

| 挂载点 | 形式 | 容量（初值） | 内容 |
|---|---|---|---|
| `/srv/render/up` | 独立 ext4 镜像，loop 挂载 | 4 GiB | Worker 暂存区 |
| `/srv/render/down` | 独立 ext4 镜像，loop 挂载 | 2 GiB | 任务、控制、确认，以及 publish 的 `.staging`（同一文件系统，可原子 rename） |
| `/srv/render-data` | 独立 ext4 镜像，loop 挂载 | 6 GiB | ingest 私有区和 inbox（同一文件系统，可原子 rename） |
| `/srv/agent` | 独立 ext4 镜像，loop 挂载 | 12 GiB | 沙箱家目录、源码、outbox |
| 合计 | | 24 GiB | |

写满任何一个都只影响它自己。挂载选项：启用 ACL，`nodev,nosuid`；`up`、`down` 和 `render-data` 加 `noexec`。

**镜像必须预分配，不得是稀疏文件**：

1. 创建：使用 `fallocate -l <大小>` 为后备文件实际分配全部块，不使用 `truncate` 或带 `seek` 的 `dd`，因为它们会生成稀疏文件。创建后核对 `stat` 的已分配块数 × 512 ≥ 文件大小；不满足则不挂载。
2. 防止事后变稀疏：loop 设备会把内部文件系统的 discard 转成对后备文件的打洞，镜像会重新变成稀疏文件。因此：
   - 挂载时使用 `nodiscard`；
   - 覆盖 `fstrim.service`，使其不处理这些挂载点（Debian 默认的 fstrim 定时器会处理所有已挂载、支持 discard 的文件系统）；
   - 后备文件放在 root 拥有、0700 的目录（如 `/var/lib/render-images/`），其他账户无法访问。
3. 巡检：root 定时任务每天核对每个后备文件的已分配块数与根分区可用空间，**只告警，不自动补齐**。发现分配缺口时，管理员先确认根分区可用空间减去缺口后仍满足下方的保留规则，再手动执行 `fallocate` 补洞；不满足就先缩小镜像或清理空间。结果写入管理员日志，不进入 inbox。镜像容量和空间回收控制都在部署时验证（FS-14 至 FS-16）。

**根分区保留**：

- 部署前（A，会话外）核对根分区可用空间。2026-10-08 只读核查时根分区约 58 GB、可用约 45 GB；该数值在部署时须重新确认。
- 规则：预分配后根分区可用空间必须 ≥ max(10 GiB, 根分区容量的 20%)。不满足就按比例缩小镜像（优先缩小 `/srv/agent`），不降低保留量。按当前数值估算，分配 24 GiB 后约剩 21 GB，满足规则。
- 除上述镜像外，本项目在根分区只写入小型状态（`/var/lib/render-*` 的账本和索引，各设大小上限）和 systemd 日志（设置 `SystemMaxUse=`）。
- 沙箱内的 Claude 在根分区没有可写路径，只能写 `/srv/agent` 和有大小上限的 tmpfs。
- ext4 默认 5% 的 root 保留块不计入上述保留量。所有服务都以非 root 账户运行，用不到这部分空间。

### 4.2 所有者与权限（修正 v0.1 报告中 `2730` 的错误）

v0.1 写的 `incoming 2730` 有两处错误：组只有写和进入权限，没有读权限，ingest 无法列举目录；上传者作为所有者实际上可读写，不能称为“只写”。修正如下：

| 路径 | 所有者:组 | 模式 | 默认 ACL | 实际能力 |
|---|---|---|---|---|
| `/srv/render` | root:root | 0755 | — | chroot 根（sshd 要求 root 拥有且不可被他人写） |
| `/srv/render/up`（文件系统根） | render-worker:render-ingest | 2750 | `u::rwx g::r-x o::--- m::r-x` | Worker 可管理自己的暂存区（创建、列举、stat、重命名、删除）；ingest 只能读和进入；其他人无权限 |
| `/srv/render/down`（文件系统根） | root:root | 0755 | — | — |
| `down/{jobs,control,handoff}` | render-publish:render-worker | 2750 | `g::r-x o::---`；**r5.1：`down/jobs` 和 `down/control` 增加 `u:render-ingest:r-x`（访问 ACL 和默认 ACL 都加）** | publish 可写；Worker 只读；ingest 只读 jobs 和 control（XF-R 检查需要 job.json，并需读取取消请求） |
| `down/acks` | render-ingest:render-worker | 2750 | 同上 | ingest 可写；Worker 只读 |
| `down/.staging` | render-publish:render-publish | 0700 | — | Worker 看不到未发布的内容 |
| `/srv/render-data`（文件系统根） | root:root | 0711 | — | 只允许进入，不允许列举 |
| `/srv/render-data/ingest` | render-ingest | 0700 | — | 私有快照、状态、隔离区 |
| `/srv/render-data/inbox` | root:render-claude | 0750 | 访问 ACL：`u:render-ingest:--x`、`u:render-publish:--x`、`u:render-netcheck:--x` | claude-agent（`render-claude` 组）可列举和读取；三个服务只能**进入**，以到达各自的子目录 |
| `inbox/{jobs,workers,handoff}` | render-ingest:render-claude | 2750 | `g::r-x o::---`；**r5.1：`inbox/jobs` 增加 `u:render-publish:r-x`（访问 ACL 和默认 ACL 都加）** | ingest 可写；Claude 只读；publish 只读 jobs（判断取消时任务是否已处于终态） |
| `inbox/submissions` | render-publish:render-claude | 2750 | 同上 | |
| `inbox/netcheck` | render-netcheck:render-claude | 2750 | 同上 | |
| `/srv/agent`（文件系统根） | root:root | 0711 | — | publish 可以进入，以到达 outbox；不能列举 |
| `/srv/agent/home`、`/srv/agent/work` | claude-agent | 0700 | — | 只有 Claude 能访问 |
| `/srv/agent/outbox` | claude-agent:render-publish | 2750 | `g::r-x o::---` | Claude 可写；publish 只读 |

- Worker 不能读取 inbox、ingest 私有区、`/etc/render`、其他账户的家目录（chroot 之外都不可见）。
- 上传者可以用受限权限创建文件，使 ingest 无法读取。结果只是自己的上传失败（判 `rejected: unreadable`），可以接受。
- ingest 对 `up/` 只读，清理由 Worker 根据确认完成（protocol 5.2）。
- **机器可读的权限表**（r5.1）：`deploy/access-matrix.json` 是本节的机器可读形式；`server/render_gates/access.py` 声明每个身份实际访问的路径，测试 SL50 检查两者一致，部署后由 FS-17 对照真实 ACL。
- **祖先目录**：每个服务写入的目标，其所有祖先目录都必须给该服务至少“进入”（x）权限。部署时用 FS-17 逐个服务、逐个路径验证能否到达，避免出现子目录属主正确、服务却到不了的情况。

### 4.3 SFTP 限制

```text
Match User render-worker
  ChrootDirectory /srv/render
  ForceCommand internal-sftp -u 0027 -p open,close,read,write,lstat,fstat,opendir,readdir,remove,mkdir,rmdir,realpath,stat,rename,posix-rename,fsync,limits
  AuthenticationMethods publickey
  PasswordAuthentication no
  PermitTTY no
  AllowTcpForwarding no
  AllowStreamLocalForwarding no
  AllowAgentForwarding no
  X11Forwarding no
  PermitTunnel no
```

- 未放行：`setstat`、`fsetstat`、`lsetstat`（Worker 不能改 mtime 和权限）、`symlink`、`hardlink`、`readlink`、`copy-data`、`statvfs`、`expand-path`、`home-directory`、`users-groups-by-id`。本 VPS 的 sftp-server 已确认支持按请求名过滤，支持的请求列表已核对。
- 来源限制：`AllowUsers` 或 `authorized_keys` 的 `from=` 只允许 Tailnet 地址段。具体写法放在 sshd 配置中，不写进交接文档。
- 连接数：Worker 使用两条独立连接，一条只传心跳和状态，一条传产物（protocol 6.5）。注意：`MaxSessions` 只限制**单条连接内**的会话数，`MaxStartups` 只限制**未完成认证**的并发连接，二者都不能限制一个账户的连接总数，也不能为某个用户预留连接。v1 不依赖连接数限制；如需限制，在部署阶段另用 nftables 按来源限制连接数，作为独立变更。
- `authorized_keys`：`restrict` 加来源限制；公钥注释不含身份信息。
- 主机密钥固定：用户在自己的终端（不经 Claude 会话，不用 `!` 前缀）读取 VPS 主机密钥指纹，经独立可信渠道写入 Worker 的 known_hosts；Worker 使用 `StrictHostKeyChecking yes`。
- 加固 root 登录和密码登录作为**单独的变更**处理，不与本项目捆绑（见 v0.3 回应第 3 节）。

## 5. 隐私判断分级

关于历史数据，统一使用三级表述，不越级下结论：

1. **可能暴露**：某个来源可被访问（例如 `tailscale status` 可调用）。
2. **本地存在**：会话外的确定性检查确认某文件含有目标值。
3. **证实进入模型请求**：确认该值出现在实际发送给模型的内容中（例如会话转录中的工具结果）。

v0.1 报告中关于 `~/.claude` 那 2 个文件“应视为已经暴露”的说法过度，现撤回。在会话外核查前，它们只属于“可能暴露”。

## 6. 已知残余风险（不声称已消除）

- 本机侧边界（渲染子进程网络隔离、个人目录访问、Worker 凭据隔离）由本机 Codex 实现和验收，VPS 端无法独立证明。
- 文本规则不能识别任意编码、混淆或隐写；v1 依靠源头隔离来降低风险。
- **执行边界**：沙箱内 Claude Code 的所有工具执行（包括会话中以 `!` 前缀运行的命令、hooks、它启动的 MCP 子进程）都在沙箱内进行，与 Claude 受同样约束；它们的输出仍会进入会话。残余风险是用户把**沙箱外**终端的输出粘贴或转述进会话，这属于操作纪律。任何在宿主上执行的入口（IDE 集成、指向宿主服务的本地 MCP、沙箱外的 tmux 客户端等）都必须禁用，由 SB-11 验证。
- Claude Code 自身发送给 Anthropic 的遥测内容未在本项目中验证；环境白名单和挂载白名单使其无法接触相关来源。
