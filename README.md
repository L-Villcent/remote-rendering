# Remote Rendering

在 VPS 上通过 SSH 使用本机（带显卡的 Windows 电脑）的 GPU 和 CPU 做渲染与计算。

VPS 经 Tailscale 内网，用 SSH 登录本机上的一个**非管理员专用账户**，把任务交给本机执行，再只取回结果中的小文件（图片、日志、摘要）。本机不需要对公网开放任何端口。

**例子**：在 VPS 上用 Claude Code 开发时，可以让它写一段着色器动画，在你的电脑上用显卡渲染出几张关键帧，取回来查看后修改代码，满意后再导出完整视频。整个循环由 Claude Code 自己完成，你的电脑只负责计算。同样的通道也可以用来运行其他 GPU 或 CPU 计算任务。

## 工作方式

```text
VPS ── ssh / scp（Tailnet，密钥登录）──▶ 本机 Windows，账户 render（非管理员）
  render/rr                                 D:\ClaudeRender\tools\claude\render.ps1
  projects/<任务>/  ──── push ────▶        D:\ClaudeRender\projects\<任务>\      代码
  runs/<运行>/      ◀── 小文件 ────        D:\ClaudeRender\runs\<运行>\          工作区（定期清理）
                                            D:\ClaudeRender\output\<任务>\        成品视频（永久保留）
                                            无头浏览器（WebGL2）+ ffmpeg，或任意命令
```

- **每次运行互相独立：** 每次运行都有独立目录，记录参数、耗时和状态，成功后写入 `SUCCESS` 标记。
- **断线处理：** 在已验证环境中，SSH 前台渲染命令断线后可继续完成，VPS 会重新查询结果；其他环境需复验。
- **编码结果一致：** 视频默认使用 libx264（CRF 18，有损编码）；已验证的并行与串行流程输出逐字节相同。
- **成品集中存放：** 编码成功的视频自动发布到 `output\<任务>\`，文件名包含日期、分辨率、帧率和时长。旁边附参数记录（`.json`）和场景代码快照（`.scene.zip`），`output\index.csv` 汇总所有成品。成品以硬链接方式存放，不占双份空间。
- **工作区自动瘦身：** 每次运行结束删除浏览器临时配置；编码成功后只保留首、中、尾 3 张关键帧。旧运行可以用 `rr prune` 清理，`output` 从不自动清理。已发布成品及其参数和场景快照保留在 output 中；重新渲染的逐字节一致性仅在已验证的工具链环境中成立。

## 目录

| 路径 | 内容 |
|---|---|
| `render/rr` | VPS 端命令行 |
| `render/local/` | 本机端脚本，用 `rr tools` 上传 |
| `projects/` | 示例任务：着色器动画（`demo`）、GPU 计算（`gpu-matmul`）、最简诊断（`tri`） |
| `examples/compute/` | 不经过浏览器的本机计算示例（Node） |
| `ops/vps-harden.sh` | VPS 加固：入站防火墙、sshd、自动更新（分步执行，可回滚） |
| `docs/` | 设计说明、实测记录、SSH 配置示例；`docs/claude-skill/SKILL.md` 是 Claude Code 全局 skill 模板（复制到 `~/.claude/skills/local-gpu-render/` 并替换占位符后，任何会话遇到渲染或 GPU 计算任务都会自动走这套流程） |
| `archive/` | 早期设计（已被取代），仅作存档 |

## 准备

**本机（Windows）**
1. 新建标准用户 `render`，不要加入管理员组。
2. 启用 OpenSSH Server：只允许密钥登录，只允许 `render` 登录。
3. 防火墙：SSH 入站只允许 Tailnet 地址段（`100.64.0.0/10`），不要对公网开放。
4. 工作目录 `D:\ClaudeRender\{projects,runs,tools}`，render 账户可读写。
5. 安装 Chrome 或 Edge，以及 ffmpeg（`D:\ClaudeRender\tools\ffmpeg\bin\`），并给 render 账户授予读取和执行权限。

**VPS**
1. 生成专用密钥，把公钥放进本机 `C:\Users\render\.ssh\authorized_keys`。
2. 核对本机主机指纹后写入 `known_hosts`。
3. 参照 `docs/ssh_config.example` 配好 `Host render-local`。
4. 运行 `render/rr tools` 上传本机端脚本；运行 `ssh render-local whoami` 自检。

## 使用

```bash
render/rr push demo                               # 上传任务代码
log=$(render/rr render demo -Only "0,75,149")     # 渲染几张关键帧
render/rr wait "$log"                             # 等待完成，打印摘要
render/rr fetch <run_id> frames                   # 取回关键帧

log=$(render/rr render demo -Width 1920 -Height 1080 -Frames 900 -Encode)   # 渲染并在本机编码
render/rr wait "$log"
render/rr fetch <run_id> contact                  # 在本机核对视频，取回一张联系表
render/rr status <run_id>                         # 查询状态；render/rr runs 列出所有运行
render/rr prune --project demo --apply            # 一轮出完成品后，清理这个项目的全部过程运行
render/rr prune 14                                # 兜底：列出 14 天前的运行（只列出）；加 --apply 才删除

render/rr render gpu-matmul -Job                  # 计算任务：结果保存在 result.json
ssh render-local "node D:\path\to\script.mjs"     # 也可以直接运行任意命令
```

**原生 GPU 工具（不经过浏览器）**：
- **Blender**（Cycles 加 OptiX）：`ssh render-local "D:\ClaudeRender\tools\blender\blender.exe -b <场景.blend> -o <输出> -a"`。GPU 设备的选择可以参考本机上的 `tools\blender-gpu-probe.py`。
- **原生 WebGPU**（Dawn for Node，D3D12 后端）：`ssh render-local "D:\ClaudeRender\tools\webgpu\node.exe D:\ClaudeRender\tools\claude\run-webgpu-job.mjs <任务> n=2048"`。导出 `run(ctx)` 的任务模块不用改代码就能在这里运行。

**自定义任务**：在 `projects/<名称>/index.js` 写一个 ES 模块：
- **渲染：** 导出 `setup(ctx)` 和 `renderFrame(i, t)`，其中 `t = i / fps`，画面只由帧号决定，不依赖实时播放。
- **计算：** 导出 `async run(ctx)`，返回 JSON。

常用参数：`-Width -Height -Fps -Frames -Only -Encode -Title -NoPublish -KeepFrames -Crf -Job`；完整说明见 `render/local/render.ps1` 开头的注释。

## 注意事项

- 本机与 VPS 之间的带宽通常有限，尽量在本机完成处理，只取回小文件。
- 通过 SSH 运行的程序在 Windows 的非交互会话（session 0）中，无头浏览器需要加 `--use-angle=vulkan --disable-gpu-compositing`（已是默认）。浏览器内的 WebGPU 在这种环境下不可用；需要 WebGPU 时请改用上面的原生 Dawn 运行时。
- 需要用到的本机工具（ffmpeg、Blender 等）必须让 render 账户有读取和执行权限。
- 本机关机或重启会中断任务，需要重跑；渲染期间会自动阻止 Windows 进入睡眠。

更多细节和实测数据见 `docs/简化方案-v1.md`。
