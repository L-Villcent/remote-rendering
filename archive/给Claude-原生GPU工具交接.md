# 原生 GPU 工具已就绪

两项均已通过真实 render SSH 会话、session 0 的验证。

## Blender

- 程序：`D:\ClaudeRender\tools\blender\blender.exe`
- 版本：4.5.14 LTS，官方 Windows 便携包，已核对 SHA256。
- Cycles OptiX 使用 NVIDIA GeForce RTX 4060 Ti，CPU 禁用。
- 默认场景 512×512、64 samples，实测渲染部分 1.248 秒；不含启动，不代表复杂场景性能。
- 探测代码：`D:\ClaudeRender\tools\blender-gpu-probe.py`，可参考其设备选择逻辑。
- 项目和输出继续放在 `D:\ClaudeRender\projects` 与 `D:\ClaudeRender\runs`。

## WebGPU

- 原生程序：`D:\ClaudeRender\tools\webgpu\node.exe`
- 实现：Dawn Node WebGPU `webgpu@0.6.2`。
- 采用 **d3d12** 后端。该构建的 Vulkan 加载失败，但 d3d12 在 session 0 可以使用 NVIDIA 硬件。
- 验证不是只枚举适配器：实际运行 WGSL compute shader 并读回结果，256 个元素全部正确。
- 示例代码：`D:\ClaudeRender\tools\webgpu\webgpu-probe.mjs`。
- 项目中的模块可从 `file:///D:/ClaudeRender/tools/webgpu/node_modules/webgpu/index.js` 导入 `create` 和 `globals`，参考探测代码管理 GPU 生命周期。
- 这是原生 Node 路线；Chrome 内的 WebGPU 没有修复，也不提供浏览器的 DOM、Canvas、HTMLVideoElement 等接口。现有 WebGL2 动画流程保持使用 Chrome。

## 复验

```bash
ssh render-local "powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\ClaudeRender\tools\Test-NativeGpu.ps1"
```

该入口会为本次测试建立独立输出目录，执行 Blender 渲染与 WebGPU 计算，打印结果及退出码。

工具目录均已显式授予 render 读取执行权限。未设置自动登录，未保存 render 密码，未新增常驻服务。测试使用的临时 SSH 公钥及私钥已移除。诊断日志只保存在本机，不上传公开仓库。
