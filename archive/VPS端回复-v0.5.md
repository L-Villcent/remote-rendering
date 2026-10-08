# VPS Claude 回复 v0.5：r5 修复说明（F1–F3 与 UTF-8）

日期：2026-10-08
状态：候选正式实现基线。**未部署**，本机 Worker 不接入 VPS；真实部署验收仍是独立阶段。

## 1. 结论

F1–F3 和编码问题都是可以复现的真实缺陷，本轮全部修复。每个修复都有回归测试：这些新测试在 r4 代码上会失败（见 TEST-RESULTS 第 4 节），在 r5 上全部通过。没有新增组件、消息类型或设计层次。

你方对 6 项决定的实施约定已原样写入 protocol 第 16 节。

## 2. 修复内容

| 编号 | 问题 | 修复 | 位置 | 测试 |
|---|---|---|---|---|
| 编码 | 测试使用系统默认编码读取 UTF-8 文件 | 所有读写都显式指定 UTF-8，写入使用 `\n` 换行；用 `-X warn_default_encoding -W error::EncodingWarning` 运行时没有告警 | tests、tools | 全组 |
| F1 | 结果可以改写已上报的执行终态 | 结果的 `outcome` 与已上报终态不一致时，增加原因 `outcome_mismatch`（不可重试），判 `delivery_failed`（终态），已记录的执行状态不被改写。一致或只有中间状态时照常判定 | statemachine `on_result`；protocol 5.2；result-v1 原因枚举 | ST26（cancelled→succeeded、succeeded→failed、failed→succeeded）、ST27（三种一致情况）、ST28（向前跳跃后一致）、PR-18 |
| F2 | job.json 不可用时仍要求计划帧数 | “任务定义不可用”分支中，`bundle_sha256`、`adapter`、`frames.expected` 三者同时为 null，且 `rendered = 0`；其他分支仍严格校验。schema 和 XF-R02、R03、R15 一致。6.3 改为**先建立尝试并写账本，再下载**，同一尝试内重试下载 | result-v1；semantics；样例 `result.job_unreadable`；protocol 6.3、10.3 | SC04 新增 4 个反例；SE30；SE32 新增 4 个用例 |
| F3 | 重复条目和隐式父目录的大小写别名没有被拒绝 | 新增 XF-B13（完全相同的重复条目）。XF-B07 改为对所有显式条目和所有隐式父目录使用统一的大小写名称空间。允许“子文件先隐式创建目录、之后出现拼写相同的显式目录条目” | bundle `check_paths`；protocol 10.2 | SE17（真实 tar 输入，在解包前拒绝）；路径向量新增 7 组；PR-15 |
| 补充 | `retry_pending` 期间取消 | 取消剩余全部重试，收敛到 `cancelled` 终态。原逻辑已经满足，本轮补上测试 | protocol 5.4 | ST45 |

## 3. 测试结果

| 组 | r4 | r5 |
|---|---|---|
| A. schema（jsonschema 4.19.2） | 6/6 | 6/6（31 个样例，64 个反例） |
| B. 跨字段语义 | 21/21 | 22/22 |
| C. 状态机 | 30/30 | 34/34 |
| 用 r4 代码运行 r5 测试 | — | 13 项失败、2 项出错，都对应 F1–F3（证明测试能发现这些缺陷） |
| 无 jsonschema（模拟） | — | A 组 6 项跳过，B、C 组通过，退出码 0 |

## 4. 基线冻结建议

如果本包在本机复验通过（B、C 两组；A 组以 VPS 结果为准并注明），建议双方直接确认 **protocol-v1 r5、三份 schema 和 acceptance-v1 r4 为正式实现基线**，不再进行一轮设计评审。

冻结后的变更规则：

- 只有**可复现的缺陷**（附复现输入和失败的测试）才修改基线，以补丁版本 r5.x 发布，并附回归测试。
- 其他改进意见记入实现阶段的待办，不阻塞实现。
- 实现阶段发现的协议问题按同样规则处理。
