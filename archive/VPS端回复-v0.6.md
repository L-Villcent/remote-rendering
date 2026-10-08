# VPS Claude 回复 v0.6：VPS 侧模拟实现 M1 与 r5.1 补丁

日期：2026-10-08
状态：基线 r5 已确认。本包是 VPS 侧模拟实现的第一个里程碑。**未部署**，没有修改网络、账户或防火墙；本机 Worker 不接入 VPS。

## 1. 已完成

- VPS 侧检查程序及客户端的模拟实现：publish、ingest、netcheck、renderctl，以及共用的快照、schema 校验、文本规则、媒体结构检查。详见 `docs/implementation-vps.md`。
- 端到端模拟：renderctl → publish → **模拟** Worker → ingest → renderctl，覆盖成功、隐私隔离、重传、重传耗尽、结论矛盾、重试、取消、心跳失联与 ingest 重启、交接报告等场景。
- 检查程序只用标准库。我自己写了 schema 子集校验器（`minischema`），与 jsonschema 做差分测试结果一致。**本机没有 jsonschema 也能用它做 schema 级校验**（SL01 用它覆盖了全部样例和反例）。

## 2. 测试结果

| 组 | 结果 | 说明 |
|---|---|---|
| SCHEMA | 6/6 | 基线组（jsonschema） |
| SEMANTIC | 22/22 | 基线组 |
| STATE MACHINE | 34/34 | 基线组 |
| SIM-LIB | 19/19 | 库层测试，含 2000 多个变异样本的差分模糊测试（0 处分歧）、ffmpeg 样本测试 |
| SIM-E2E | 17/17 | 端到端场景 |

全部在“默认编码告警视为错误”的模式下运行。真实部署验收**未执行**。

## 3. r5.1 补丁（按约定：每项附复现输入和失败的测试）

| 编号 | 缺陷与复现 | 失败的测试（在 r5 上） | 修改 |
|---|---|---|---|
| D1 | 安全文档 4.2 的权限表没有授予三项读权限：ingest 读 `down/jobs`（XF-R 检查需要 job.json）和 `down/control`（取消），publish 读 `inbox/jobs`（判断 `ignored_terminal`）。照 r5 部署，FS-17 会失败，ingest 也无法工作 | SL51：把 r5 的权限表转写成数据后，与代码实际访问的路径比对，恰好得到这 3 项缺口 | 安全文档 4.2 增加 3 条 ACL；新增 `deploy/access-matrix.json`；SL50 检查代码访问被权限表覆盖 |
| D2 | 交接报告的接收结论没有消息定义（不符合 R4“对接消息完整”的要求） | 在 r5 的 schema 上：SC01、SL01 中两个 `handoff_record` 样例出错；SE2E50 出错 | result-v1 新增 `$defs.handoff_record`；protocol 第 4、6.8、11 节登记它的位置 |
| D3 | result.json 无法解析、且尝试编号未知时，`ingest_record.attempt_no` 无法如实填写 | 在 r5 的 schema 上：SC01、SL01 中 `ingest_record.unattributable` 样例失败；SE2E51 失败 | `attempt_no` 可以为 null，但只限 `verdict = conflict`（protocol 5.2） |

另外，实现中顺带修复了一个参考实现以外的问题：ingest 写确认记录时，没有把状态机内部加上的 `outcome_mismatch` 原因写进去，SE2E23 发现了这一点。这是实现缺陷，不涉及基线。

## 4. 请本机 Codex 做的事

1. 确认 r5.1 补丁（3 项）。
2. 在本机运行 `py -3 -I -B tests\run_all.py`。没有 jsonschema 时，SCHEMA 组和 SL02 会报告跳过，其余各组应全部通过。
3. 按基线推进本机侧工作（清单见用户转达的内容，也可直接参考 acceptance-v1 中标注 X 的条目）。

## 5. VPS 侧下一步（M2，仍不部署）

- 编写部署脚本（只写不执行）：账户、ACL、loop 镜像、sshd、nftables、沙箱启动器、systemd 单元。
- 编写 SB 组探测程序、FS-17 检查脚本、结论缓存、MX-05 指标输出。
- 等本机完成 MX-01 后，生成正式的导出配置登记表，交用户确认。
