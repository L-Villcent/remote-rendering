# Remote Rendering：让 VPS 上的 Claude Code 调用本机 GPU

> 实验项目：已在一组 Windows + NVIDIA + Linux VPS 环境中验证。性能数据是该环境的实测结果，不代表其他机器的保证；`archive/` 为已停用的历史设计。

Claude Code 运行在 VPS 上，本机是一台带 NVIDIA 显卡的 Windows 电脑。VPS 通过 Tailscale 内网用 SSH 登录本机的一个**非管理员专用账户**，在本机用 GPU 渲染 WebGL 着色器动画并完成编码，也可以运行其他计算任务；VPS 只取回关键帧、联系表和日志这类小文件。工作循环是：写代码 → 渲染关键帧 → 看图 → 修改 → 导出视频。

```text
VPS（Claude Code）── ssh / scp（Tailnet，密钥登录）──▶ 本机 Windows，账户 render（非管理员）
  render/rr                                            D:\ClaudeRender\tools\claude\render.ps1
  projects/<场景>/index.js  ── push ──▶              D:\ClaudeRender\projects\<场景>\
  runs/<运行编号>/  ◀── 关键帧、联系表、日志 ──     D:\ClaudeRender\runs\<运行编号>\
                                                       无头 Chrome（WebGL2 / Vulkan）→ PNG → ffmpeg（libx264）
```

## 目录

| 路径 | 内容 |
|---|---|
| `render/rr` | VPS 端命令行（bash） |
| `render/local/` | 本机端工具，用 `rr tools` 上传到 `D:\ClaudeRender\tools\claude\`：`render.ps1`（运行器）、`harness.html` 和 `harness.js`（浏览器外壳）、`list-runs.ps1`、`inspect-run.ps1` |
| `projects/` | 场景和计算任务：`demo`（流体加光球动画）、`gpu-matmul`（GPU 矩阵乘法）、`tri`（最简诊断场景） |
| `examples/compute/` | 不经过浏览器的本机计算示例（Node 多核） |
| `ops/vps-harden.sh` | VPS 加固脚本：入站防火墙、sshd、自动安全更新，分步执行，可回滚 |
| `docs/简化方案-v1.md` | 方案说明、验证记录与实测数据 |
| `docs/ssh_config.example` | VPS 端 `~/.ssh/config` 示例 |
| `archive/` | 早期的协议式设计（任务队列、检查程序、部署脚本、评审往来），已被简化方案取代，仅作存档 |

## 前置条件

**本机（Windows）**
- NVIDIA 显卡和驱动；Google Chrome 或 Microsoft Edge。
- 一个标准用户 `render`，不加入 Administrators。
- Windows OpenSSH Server：`PasswordAuthentication no`、`PubkeyAuthentication yes`、`AllowUsers render`。
- 防火墙：SSH 入站只允许 Tailnet 地址段（`100.64.0.0/10`），**不要对公网开放 22 端口**。
- 工作目录 `D:\ClaudeRender\{projects,runs,tools}`，render 账户有读写权限。
- ffmpeg 放在 `D:\ClaudeRender\tools\ffmpeg\bin\`，**并给 render 账户授予读取和执行权限**，例如：`icacls D:\ClaudeRender\tools\ffmpeg /grant "<计算机名>\render:(OI)(CI)RX" /T`。
- 可选：Node（用于 `examples/compute`）。

**VPS（Linux）**
- bash、OpenSSH 客户端、Tailscale；python3 用于解析 JSON 输出（可选）；ffmpeg 可选。

## 一次性配置

1. 在 VPS 上生成专用密钥：`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_render -C render`。把公钥放进本机的 `C:\Users\render\.ssh\authorized_keys`。
2. 核对本机主机指纹：在本机运行 `ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub`，与 VPS 上 `ssh-keyscan -t ed25519 <主机> | ssh-keygen -lf -` 的结果比对，一致后再写入 `~/.ssh/known_hosts`。
3. 参照 `docs/ssh_config.example` 写好 VPS 端的 `~/.ssh/config`（`Host render-local`）。示例使用连接复用，先执行 `mkdir -p ~/.ssh/cm && chmod 700 ~/.ssh ~/.ssh/cm`。
4. 上传本机端工具：`render/rr tools`。
5. 自检：`ssh render-local whoami`。

## 日常使用

```bash
render/rr push demo                                      # 上传 projects/demo（替换本机上的副本）
log=$(render/rr render demo -Only "0,75,149")            # 只渲染关键帧（PNG，不编码）
render/rr wait "$log"                                    # 等待完成，打印 JSON 摘要
render/rr fetch <run_id> frames                          # 取回关键帧查看

log=$(render/rr render demo -Width 1920 -Height 1080 -Fps 30 -Frames 900 -Encode)
render/rr wait "$log"
render/rr fetch <run_id> contact                         # 在本机核对视频（ffprobe），取回 4 帧联系表
render/rr fetch <run_id> video                           # 需要时再取回完整视频
render/rr status <run_id>                                # succeeded | failed | running | interrupted
render/rr runs                                           # 列出本机上的所有运行
```

**场景约定**：`projects/<名称>/index.js` 是 ES 模块，导出 `setup(ctx)` 和 `renderFrame(i, t)`，其中 `t = i / fps`。动画只能由帧号和种子决定，不依赖实时播放。`ctx` 包含 `gl`（WebGL2）、`canvas`、`width`、`height`、`fps`、`frames`、`seed`。

**计算任务**：模块改为导出 `async run(ctx)`，返回一个 JSON 对象，用 `render/rr render <名称> -Job [-JobParams '{"n":2048}']` 运行，结果保存在运行目录的 `result.json`。示例见 `projects/gpu-matmul`。

**任意命令**：`ssh render-local "<命令>"`，例如 `node D:\...\cpu-primes.mjs`。PowerShell 脚本建议先上传成文件再用 `-File` 执行，内联命令的引号转义很容易出错。

### render.ps1 主要参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `-Width -Height -Fps -Frames -Seed` | 1280 720 30 150 42 | |
| `-Only "0,75,149"` | 无 | 只渲染这些帧，适合关键帧检查 |
| `-Encode` | 关闭 | 渲染全部帧并编码成 `video.mp4`，编码与渲染同时进行 |
| `-Mode png\|stream` | png | png 模式保存每帧 PNG；stream 模式直接送原始像素（画质相同，但更慢，不推荐） |
| `-Inflight` | 4 | 并行做 PNG 压缩的帧数 |
| `-Crf` | 18 | libx264 质量参数，preset 为 slow |
| `-VerifyMd5` | 关闭 | 输出送进编码器的每帧像素的 MD5（`logs\input.framemd5`） |
| `-Job [-JobParams]` | 关闭 | 计算任务模式 |
| `-ExtraArgs` | `--disable-gpu-compositing` | 额外的 Chrome 参数 |

### 运行目录

每次运行都新建 `runs\<项目>-<UTC时间>-<随机4位>\`，从不覆盖已有目录。里面有：
- `scene\`：场景代码快照；
- `frames\`：PNG 帧；
- `logs\`：运行器、浏览器、ffmpeg 的日志，以及 GPU 占用采样；
- `video.mp4` 或 `result.json`；
- `run.json`：参数、分段耗时、GPU 占用峰值、渲染器、状态；
- `SUCCESS`：最后写入，加了 `-Encode` 时在编码完成后才写。

## 实测数据（RTX 4060 Ti、Ryzen 5 7500F）

| 任务 | 结果 |
|---|---|
| 1280×720，5 秒，150 帧，渲染加编码 | 本机约 7 秒 |
| 1920×1080，30 秒，900 帧，渲染加编码 | 43.6 秒（原来的串行方式为 90.3 秒）；输出视频与串行方式**逐字节相同** |
| WebGL2 计算 2048×2048 单精度矩阵乘法 | 每次 25 毫秒，约 679 GFLOPS，抽查 64 个元素与 CPU 结果一致 |
| Node 12 线程统计 2 亿以内的质数 | 0.47 秒，结果正确 |
| VPS 与本机之间经 Tailnet 传输 | 约 1–2 MiB/s，因此不回传整套帧，只取小文件 |

## 已知限制与坑

以下行为来自当前测试环境。Chrome、驱动和 Windows OpenSSH 更新后，请重新验证 GPU 后端与断线行为。

- 这是供可信操作者使用的远程执行工具，SSH 账户可执行其权限范围内的任意代码；标准用户不等于沙箱。请先核对个人文件与其他磁盘目录的实际访问权限。
- `ops/vps-harden.sh` 是可选的管理员工具，含环境假设（例如账户名 `code` 和 Tailscale UDP 端口）；阅读并调整后再分步运行，不是渲染流程的必需步骤。

- **通过 SSH 运行的程序在 session 0 中**：无头 Chrome 必须加 `--use-angle=vulkan --disable-gpu-compositing`，否则 WebGL 上下文会丢失。**WebGPU 在这种环境下拿不到 GPU 适配器**，GPU 计算要改用 WebGL2。
- **SSH 断开**：作为会话前台命令运行的渲染，在断开后会继续完成，`rr wait` 会改为查询本机状态。但如果程序是在后台启动的，而那条 SSH 命令本身先结束了，这些后台进程会被 Windows 清理掉。
- render 账户没有 WMI 权限，也不能不提供密码就创建计划任务；需要无人值守的长任务时，要由管理员另行配置。
- `Get-PSDrive` 在 render 账户下显示可用空间为 0，应该改用 `[IO.DriveInfo]`。
- 本机关机、重启或进入睡眠，都会中断渲染。`render.ps1` 会请求阻止睡眠，但是否生效需要管理员用 `powercfg /requests` 确认。
- PowerShell 5.1 的脚本只用 ASCII 字符，因为不带 BOM 的非 ASCII 字符会被读错。

## VPS 加固

`sudo ops/vps-harden.sh check | firewall | confirm | updates | sshd | rollback`：
- 防火墙只放行回环、`tailscale0` 和 Tailscale 的 UDP 端口；
- 防火墙规则 5 分钟后会自动撤销，除非运行 `confirm`；
- 改 sshd 之前会检查确认改完后仍能拿到 root。
