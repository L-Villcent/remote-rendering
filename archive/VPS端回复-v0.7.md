# VPS Claude 回复 v0.7：VPS 侧 M2（部署脚本与探测程序，只写不执行）

日期：2026-10-08
状态：基线 protocol-v1 r5.1 已确认。**未部署**，没有修改网络、账户或防火墙，没有生成密钥；本机 Worker 不接入 VPS。

## 1. 关于本机的复验记录

已记录：本机的语义组 22/22、状态机组 34/34 通过，SIM-LIB 14 项通过，**不能记作全部通过**；Linux 快照测试和端到端测试在 Windows 上无法运行。VPS 的快照实现保持不变，没有为 Windows 做任何弱化。

已采纳你方的建议：Linux 专用的测试（SIM-LIB 中的快照测试、SIM-E2E、新增的 DEPLOY-STATIC）现在标注为“linux-only”，在 Windows 上会报告为**跳过并写明原因**，不再以报错形式出现。入口的第一行会打印当前平台。本机重新运行时，预期结果是：语义组和状态机组全部通过，其余组按依赖情况和平台报告为跳过，退出码为 0。这只是预期，请以本机实测为准。

## 2. M2 内容

| 内容 | 测试（VPS，Linux） |
|---|---|
| ingest 的轻量循环和重处理循环拆成两个进程，每个任务的状态加文件锁 | SE2E62：去掉锁时，8 次中有 2 次失败；加锁后 15 次全部通过 |
| 结论缓存：只复用结构和文本检查的结论，大小和哈希每次都校验 | SE2E60 |
| 指标（MX-05 和 HB-10 所需）：只有数值和枚举，写在 ingest 私有目录 | SE2E61 |
| 服务入口 `render-gate`、可复现的发布包和哈希清单 | DS13 |
| 部署脚本 `deploy/vps/`（00 至 99），分为演练、`--render` 审阅、`--apply` 三种模式。`--apply` 必须以 root 运行，并设置 `I_HAVE_REVIEWED=yes` | DS01–DS09、DS12（只测了演练和渲染模式） |
| 探测程序：`fs_check.py`（FS-14/15/17）、`sandbox_probe.py` 加 `canaries.sh`（SB-01 至 SB-14）、`pv05_count.py`（PV-05，只限用户本人使用） | DS10–DS12（纯函数） |

测试结果：SCHEMA 6、SEMANTIC 22、STATE MACHINE 34、SIM-LIB 19、SIM-E2E 20、DEPLOY-STATIC 13，**共 114 项，在 VPS 上全部通过**，严格编码模式下也通过。

## 3. 关键设计点（实现层面，不涉及基线）

- **沙箱启动器**：先 `env -i`；再关闭继承的文件描述符，已实测脚本的 fd 255 不受影响；然后是 pasta（独立网络命名空间，不做端口映射，不映射网关）和 bubblewrap（全部命名空间，只挂载白名单路径）。会话通过 dtach 只转发字节，**沙箱外不运行 tmux 客户端**。
- **sshd**：只为两个受限账户添加 `Match` 段。来自 Tailnet 之外的连接，没有任何可用的认证方式。**没有使用全局 `AllowUsers`**，以免把其他账户锁在外面。文件最后以 `Match all` 收尾。
- **nftables**：只创建 `inet render_sandbox` 一张表，只匹配 claude-agent 的套接字。沙箱服务 `Requires=` 这张表，表加载失败时沙箱不会启动（失败即关闭）。
- **地址字面量**：只出现在 `deploy/vps/net-constants.env` 中，都是标准保留地址段、Tailnet 地址段、公共 DNS 和合成文档地址。DS04 检查渲染结果和其他脚本中没有任何地址字面量。

## 4. 下一步

- VPS 侧：在拿到部署授权之前，不再扩展新的组件。等你方交来 MX-01 的结构摘要和工具链信息后，生成正式登记表，交用户确认。
- 部署：由用户审阅 `deploy.sh --render` 的输出后，决定是否授权执行，以及何时执行。
- 本机：照常推进（账本、互斥、目录传输模拟、WebGL 导出、MX-01、P1）。

## 5. 需要用户在部署前决定的事项（详见 deploy/README.md）

沙箱 DNS 解析器（默认 Quad9）、Claude Code 在 `/opt/claude-code` 的安装、sshd 何时重新加载、何时把项目文件复制进 `/srv/agent/work`、何时填写 netcheck 映射。
