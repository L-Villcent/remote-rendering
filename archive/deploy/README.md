# VPS 部署脚本（M2：只写不执行）

状态：脚本只在**演练**和**渲染**模式下经过测试（tests/test_deploy.py）。`--apply` 路径**从未执行过**，需要用户审阅并授权后，由用户本人以 root 运行。

## 1. 三种模式

| 命令 | 作用 | 是否改动系统 |
|---|---|---|
| `deploy/vps/deploy.sh [步骤…]` | 演练：打印每条命令和每次文件写入（只显示路径、权限、大小） | 否 |
| `deploy/vps/deploy.sh --render DIR [步骤…]` | 把要写入系统的所有文件渲染到 DIR 下，供逐个审阅；DIR/.modes 中列出每个文件的权限和属主 | 否 |
| `I_HAVE_REVIEWED=yes deploy/vps/deploy.sh --apply [步骤…]` | 真正执行，只能以 root 运行 | **是** |

步骤按顺序为：00-preflight（只读）、10-accounts、20-images、30-dirs、40-sshd、50-nftables、60-sandbox、70-gates、80-verify。`99-rollback` 只有在显式指定时才会执行（需要 `CONFIRM=1`；`PURGE=1` 会同时删除镜像和账户）。

## 2. 参数

- `deploy/vps/config.env`：账户名、镜像大小（4/2/6/12 GiB）、根分区保留规则（≥ max(10 GiB, 20%)）、Claude Code 的安装位置。
- `deploy/vps/net-constants.env`：Tailnet 地址段、拒绝访问的保留地址段、**沙箱 DNS 解析器**（默认 Quad9，请自行决定）、探测用的合成文档地址。这是包内**唯一**应当出现地址字面量的文件，其中都是公开的标准常量，不涉及本机。

## 3. 执行前必须由用户确认的事项

1. **sshd（40）**：只为两个受限账户添加 `Match` 段，不修改 root 和密码登录。执行时会运行 `sshd -t` 校验，只有设置 `RELOAD_SSHD=1` 才会重新加载。**测试时请保持一个现有会话不要断开**。
2. **nftables（50）**：只创建 `inet render_sandbox` 表，规则只作用于 claude-agent 的套接字，不修改其他规则。主机防火墙的现状仍未核实。
3. **镜像（20）**：用 `fallocate` 预分配，mkfs 和挂载都带 `nodiscard`，fstrim 只处理 `/`。巡检只告警，不会自动补齐。
4. **沙箱（60）**：需要从 apt 安装 bubblewrap、passt、dtach。Claude Code 由用户安装到 `/opt/claude-code`，并在沙箱内**重新登录**；旧的 `~/.claude` 不会被复制进去。
5. **检查程序（70）**：用 `tools/make_gate_release.py <版本>` 生成发布包和清单，审阅后再通过 `RELEASE_TAR` 和 `RELEASE_MANIFEST` 安装；安装前会逐个核对文件哈希。服务只启用、不自动启动。`/etc/render/profiles.json` 在完成 MX-01 登记之前为空，在此之前**所有媒体产物都会被拒收**。`netcheck-map.json`（root 0600）中的设备 ID 由用户填写。
6. 项目文件不会被自动移动，需要用户审阅后自行复制到 `/srv/agent/work`。

## 4. 部署后的验收（D 组）

| 工具 | 验收项 | 运行方式 |
|---|---|---|
| `80-verify.sh` → `deploy/probes/fs_check.py` | FS-01/03/04/05/06（配置层面）、FS-14、FS-15、FS-17 | root；只输出 PASS/FAIL |
| `deploy/probes/canaries.sh setup → run → teardown` | SB-01 至 SB-14（使用合成金丝雀，在全新沙箱内运行 `sandbox_probe.py`） | root，在**管理员终端**中运行 |
| `deploy/probes/pv05_count.py` | PV-05（真实地址只计数） | **只能由用户本人在独立终端中运行**，不要在 Claude 会话中运行，也不要用 `!` 前缀 |

## 5. 已知未验证项

- 所有 `--apply` 路径、bubblewrap 和 pasta 的具体参数组合、`sshd -T` 的输出格式、nft 语法，都只做过静态检查，需要在部署时实测（SB、FS 组）。
- Debian 的 `sshd_config` 是否在开头 `Include` 了 `sshd_config.d`：由 00-preflight 检查。
