# GitHub 加速器

Windows 桌面小工具：一键改写系统 `hosts`，把 GitHub 相关域名解析到**实测最快且真实可用**的官方 IP，绕过国内 DNS 污染导致的打开慢、加载失败问题。不需要时可以一键关闭，恢复系统默认解析，不留残留。

## 下载

前往 [Releases](https://github.com/hujiangming1/GitHub-Accelerator/releases/latest) 下载 `GitHubAccelerator.exe`，双击即可运行（免安装、单文件）。

> 首次运行会请求管理员权限，因为修改 hosts 需要管理员权限。

## 功能

- **一键开启 / 关闭**：开启时写入 hosts，关闭时整段删除，不影响 hosts 里原有的其他记录
- **自动优选**：从多个源获取最新 IP，并发测速后再用真实 HTTPS 请求校验，自动剔除会返回 `400`、证书不匹配、被限流的 IP
- **按域名独立选路**：`github.com`、`api.github.com`、`raw.githubusercontent.com` 等分别选择各自可用的 IP，避免"端口通但服务不对"的情况
- **离线可用**：在线源全部不可达时使用内置官方 IP 段兜底；已选好的结果会缓存，下次可跳过测速快速开启
- **安全可逆**：修改前自动备份 `hosts.bak.时间戳`，只动自身标记的区块

## 加速范围

`github.com`、`api.github.com`、`gist.github.com`、`codeload.github.com`、`github.dev`、
`raw.githubusercontent.com`、`objects.githubusercontent.com`、`avatars*.githubusercontent.com`、
`user-images.githubusercontent.com`、`private-user-images.githubusercontent.com`、
`github.githubassets.com` 等 30 个域名，覆盖网页浏览、`git clone` / `git push`、raw 文件下载。

## 使用

1. 双击 `GitHubAccelerator.exe`，在 UAC 提示中点"是"
2. 点击 **开启加速**，等待日志输出完成（约几秒）
3. 打开 GitHub 验证；若之前访问过报错页面，按 `Ctrl+Shift+R` 强制刷新
4. 不需要时点击 **关闭加速**，即恢复系统默认 DNS 解析

界面左下角的"开启前自动测速"勾选项：取消勾选则使用缓存 IP 秒开。

## 原理

国内访问 GitHub 慢，主要原因是 DNS 解析被污染或解析到不合适的 CDN 节点。本工具直接把这批域名指向官方可用 IP，从而跳过 DNS 环节。

需要注意：**仅靠端口连通和延迟并不足以判断 IP 是否可用**。例如 `api.github.com` 的 IP 对 `github.com` 的请求会直接返回 `400 Bad Request`，浏览器上看到的是：

```
Whoa there!
You have sent an invalid request.
```

所以本工具在写入前会用目标域名做 SNI 握手并发一次真实请求，只有响应正常（200/30x 等）的 IP 才会被采用，遇到 `400`、证书不匹配或超时则自动换下一个候选。

## 常见问题

**Q：打开 GitHub 仍显示 "Whoa there!"？**
A：说明 hosts 里残留了错误 IP。请用 v1.1.0 及以上版本重新点击"开启加速"（会自动覆盖旧记录），然后 `Ctrl+Shift+R` 强制刷新；如仍不行，清除一下 github.com 的 Cookie 与缓存。

**Q：日志显示某些域名没加速？**
A：这些域名的候选 IP 在当前网络下都没通过校验，程序已自动跳过，不影响其他域名使用。

**Q：杀毒软件报警？**
A：单文件 EXE 由 PyInstaller 打包，常被启发式误报，可加入信任。也可以按下方方式用源码自行运行。

**Q：会影响其他网站吗？**
A：不会。只写入 GitHub 相关域名，且带独立标记段；关闭时整段移除。

## 从源码运行 / 自行打包

```powershell
# 直接运行（需要 Python 3.9+，仅标准库）
python github_accel.py

# 打包为单文件 EXE
powershell -ExecutionPolicy Bypass -File build.ps1
```

打包产物在 `dist/GitHubAccelerator.exe`。

## 目录结构

```
github_accel.py            # 主程序（GUI + hosts 改写 + IP 测速校验）
build.ps1                  # 本地打包脚本
.github/workflows/build.yml# 推送后自动打包并发布 Release
```

## 免责声明

本工具仅做本地 DNS 解析优化，不代理、不转发任何流量，不修改除 hosts 加速段以外的系统设置。请遵守当地法律法规与 GitHub 服务条款，自行承担使用风险。
