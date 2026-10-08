# VPS 侧实现说明（模拟阶段 M1 + M2）

基线：protocol-v1 r5.1（双方已确认）。状态：只在临时目录中模拟运行；**没有部署、没有服务、没有网络访问**。

## 1. 代码布局

| 路径 | 内容 |
|---|---|
| `server/render_gates/config.py` | 目录布局 `Layout`（生产路径和模拟路径）、限额 |
| `server/render_gates/fsutil.py` | 对不可信目录的安全快照（逐级 `O_NOFOLLOW`、只接受普通文件、属主检查、链接数为 1、限长、边复制边算哈希）和原子写入 |
| `server/render_gates/minischema.py` | 标准库实现的 JSON Schema 子集校验器，只支持本协议用到的关键字，遇到未知关键字直接报错。与 jsonschema 做差分模糊测试（2000 多个变异样本，0 处分歧） |
| `server/render_gates/textrules.py` | 文本规则 v1（TX-*），不返回命中值 |
| `server/render_gates/mediacheck.py` | PNG/MP4 有界只读结构检查；首次验证用的结构文法归纳（`fingerprint`） |
| `server/render_gates/publish.py` | render-publish：提交、任务包、身份、幂等、原子发布、取消、回执 |
| `server/render_gates/ingest.py` | render-ingest：轻量循环（心跳、状态、取消、健康、视图）和重处理循环（结果、交接报告） |
| `server/render_gates/netcheck.py` | render-netcheck：状态 → 白名单视图（纯函数）；读取本地 tailscaled 套接字的函数仅供部署后使用，开发和测试中**从不调用** |
| `server/render_gates/access.py` + `deploy/access-matrix.json` | 每个身份的实际访问路径声明，以及权限表（r5.1） |
| `server/render_gates/ids.py` | ULID（VPS 时钟）和随机 attempt_id |
| `client/renderctl.py` | Claude 侧命令行（submit、receipt、status、workers、netcheck、artifacts、logs、fetch、cancel）；可复现打包；本地预检 |
| `sim/fake_worker.py`、`sim/media_fixtures.py` | 端到端测试用的**模拟** Worker 和合成媒体，不是真实 Worker |

依赖：只用 Python 标准库，并复用 `refimpl/render_protocol`。

## 2. 运行

```text
python3 -I -B tests/run_all.py                    # 五组：三组基线 + SIM-LIB + SIM-E2E
python3 client/renderctl.py --root <模拟目录> ...   # 对模拟目录操作
```

## 3. 已覆盖的验收项（模拟部分）

| 验收项 | 测试 |
|---|---|
| TX-01、TX-03、TX-05 | SL10–SL11（敏感语料与良性语料） |
| TX-02、TX-04、TX-06 | SE2E20、SE2E50、SL12 |
| MX-02、MX-03、MX-07、MX-08 | SL21–SL23（合成文件）、SE2E25 |
| MX-05（初步） | SL24：VPS 上用 ffmpeg 生成的样本。PNG 最多读约 1 KB、约 1 毫秒；MP4 最多读约 2.3 KB、约 0.2 毫秒。**正式数值以本机真实导出流程的实测为准** |
| FS-10、FS-11、FS-12 | SL30–SL32、SE2E41 |
| GT-06、GT-07、GT-08 | SL40–SL42、SE2E11 |
| PR-01、PR-02、PR-08、PR-10、PR-11、PR-13、PR-14、PR-18 | SE2E10–SE2E30 |
| HB-01、HB-04、HB-05、HB-06，以及 HB-11 的 VPS 侧部分 | SE2E40 |
| FS-17 的静态部分 | SL50（代码访问 ⊆ 权限表） |

真实 SFTP、sshd、ACL、loop 文件系统、沙箱以及本机执行，都要到部署阶段才能验收。

## 4. M2 新增

| 内容 | 位置 | 测试 |
|---|---|---|
| ingest 的轻量循环和重处理循环以两个独立进程运行：每个任务的状态读改写都加 `flock` 锁 | `ingest.py` `_locked` | SE2E62：两个独立实例并发运行。去掉锁时，8 次中有 2 次失败；加锁后 15 次全部通过 |
| 结论缓存：以（sha256、规则版本、配置版本、导出配置）为键，**只复用结构和文本检查的结论，大小和哈希每次都校验** | `ingest.py` `_cache_*` | SE2E60 |
| 指标：每个产物的读取字节数、结构数、耗时；每次交付的耗时和峰值内存；轻量循环的耗时（抽样，慢的必记）。只有数值和枚举，写在 ingest 私有目录，不进入 inbox | `ingest_private/metrics/*.jsonl` | SE2E61 |
| 服务入口 `render-gate <角色>`（publish、ingest-light、ingest-heavy、netcheck）；日志只记异常类型和 errno | `server/render_gates/service.py`、`bin/render-gate` | DS13 |
| 可复现的发布包和哈希清单 | `tools/make_gate_release.py` | DS13（解包后核对清单，并以四种角色运行） |
| renderctl 的默认路径改为沙箱视图（`/outbox`、`/inbox`） | `config.Layout.claude_view` | — |
| 部署脚本（只写不执行）：账户、预分配镜像、目录与 ACL、sshd、nftables、沙箱启动器、检查程序的 systemd 单元、核验、回滚 | `deploy/vps/`，说明见 `deploy/README.md` | DS01–DS09、DS12（只在演练和渲染模式下测试） |
| 探测程序：FS-14/15/17（`fs_check.py`）、SB-01 至 SB-14（`sandbox_probe.py` 加 `canaries.sh`）、PV-05（`pv05_count.py`，只限用户本人使用） | `deploy/probes/` | DS10–DS12（纯函数） |
| 测试平台标签：Linux 专用的组在其他平台上报告为“跳过（仅限 Linux）”，不会出错 | `tests/_common.py` `LINUX_ONLY` | — |

## 5. 待办（不阻塞）

- MX-01 完成后，由用户确认并生成正式的导出配置登记表（`/etc/render/profiles.json`）和工具链登记表。
- 部署阶段：在用户授权下执行 `--apply`，然后跑 D 组验收（SB、FS、GT、HB、PV）。
