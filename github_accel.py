# -*- coding: utf-8 -*-
"""
GitHub 加速器

原理：把 GitHub 相关域名解析到实测最快的官方 IP，写入系统 hosts，
      从而绕过国内 DNS 污染 / 解析缓慢的问题。关闭时删除写入的段落即可。

特点：
    - 一键开启 / 关闭，不改动 hosts 的其他内容
    - 自动从多个源获取最新 IP，并做 TCP 443 延迟测速优选
    - 离线可用（内置 IP 池 + 本地缓存）
    - 需要管理员权限（修改 hosts）
"""

import ctypes
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

APP_NAME = "GitHub 加速器"
APP_VERSION = "1.1.0"

HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
BEGIN_MARK = "# >>> GitHubAccelerator BEGIN >>> (由 GitHub 加速器 自动生成，关闭加速时整段删除)"
END_MARK = "# <<< GitHubAccelerator END <<<"

CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "GitHubAccelerator")
CACHE_FILE = os.path.join(CACHE_DIR, "ip_cache.json")
CONF_FILE = os.path.join(CACHE_DIR, "config.json")

# ---------------------------------------------------------------- IP 池配置
# 官方 IP 段（长期有效，作为离线兜底），靠前的优先
BUILTIN_POOLS = {
    "main": [
        "20.27.177.113", "140.82.112.3", "20.200.245.247",
        "20.201.28.151", "140.82.114.3", "20.205.243.166", "140.82.113.3",
    ],
    "raw": [
        "185.199.108.133", "185.199.109.133", "185.199.110.133", "185.199.111.133",
    ],
    "assets": [
        "185.199.108.154", "185.199.109.154", "185.199.110.154", "185.199.111.154",
    ],
}

# 各分组用真实 HTTPS 请求校验的路径；raw 分组不做 HTTP 校验
# （国内部分网络会干扰 raw 的 TLS 握手，HTTP 校验会全部误判为失败）
VALIDATE_PATH = {"main": "/", "assets": "/robots.txt"}

# 域名 -> 所属 IP 池
DOMAIN_POOL = {
    "github.com": "main",
    "api.github.com": "main",
    "gist.github.com": "main",
    "codeload.github.com": "main",
    "github.dev": "main",
    "collector.github.com": "main",
    "alive.github.com": "main",
    "api.individual.githubcopilot.com": "main",
    "copilot-proxy.githubusercontent.com": "main",
    "raw.githubusercontent.com": "raw",
    "gist.githubusercontent.com": "raw",
    "objects.githubusercontent.com": "raw",
    "objects-origin.githubusercontent.com": "raw",
    "github-releases.githubusercontent.com": "raw",
    "camo.githubusercontent.com": "raw",
    "media.githubusercontent.com": "raw",
    "avatars.githubusercontent.com": "raw",
    "user-images.githubusercontent.com": "raw",
    "private-user-images.githubusercontent.com": "raw",
    "github.githubassets.com": "assets",
}

for _i in range(10):
    DOMAIN_POOL["avatars%d.githubusercontent.com" % _i] = "raw"

# 在线 IP 源（按顺序尝试，前两个是国内基本可达的镜像）
IP_SOURCES = [
    "https://raw.githubusercontent.com/521xueweihan/GitHub520/main/hosts",
    "https://cdn.jsdelivr.net/gh/521xueweihan/GitHub520@main/hosts",
    "https://gh-proxy.com/https://raw.githubusercontent.com/521xueweihan/GitHub520/main/hosts",
    "https://ghfast.top/https://raw.githubusercontent.com/521xueweihan/GitHub520/main/hosts",
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GitHubAccelerator/" + APP_VERSION
PROBE_TIMEOUT = 1.5
PROBE_PORT = 443


# ---------------------------------------------------------------- 基础工具
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """以管理员身份重新启动当前程序。"""
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, ""
    else:
        exe, params = sys.executable, '"%s"' % os.path.abspath(__file__)
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return ret > 32
    except Exception:
        return False


def run_hidden(cmd):
    """静默执行命令，返回 (returncode, output)。"""
    try:
        p = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=0x08000000, timeout=30,
        )
        return p.returncode, p.stdout.decode("gbk", "ignore")
    except Exception as e:
        return -1, str(e)


def flush_dns():
    return run_hidden(["ipconfig", "/flushdns"])


# ---------------------------------------------------------------- 在线获取 IP
def _fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def parse_hosts_text(text):
    """从 hosts 文本中解析 {域名: [IP...]}"""
    result = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ip = parts[0]
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        for domain in parts[1:]:
            domain = domain.lower()
            if domain in DOMAIN_POOL:
                result.setdefault(domain, [])
                if ip not in result[domain]:
                    result[domain].append(ip)
    return result


def fetch_online_ips(log):
    """依次尝试各个源，成功即返回 {domain: [ip]}"""
    for url in IP_SOURCES:
        try:
            log("  尝试源：%s" % url.split("/")[2])
            text = _fetch(url)
            mapping = parse_hosts_text(text)
            if mapping:
                log("  获取成功，覆盖 %d 个域名" % len(mapping))
                return mapping
            log("  源内容为空，换下一个")
        except Exception as e:
            log("  失败（%s），换下一个" % type(e).__name__)
    log("  所有在线源均不可用，使用内置 IP 池")
    return {}


# ---------------------------------------------------------------- 测速
def probe_ip(ip, timeout=PROBE_TIMEOUT):
    """TCP 连接测试，返回毫秒；失败返回 None。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        t0 = time.perf_counter()
        s.connect((ip, PROBE_PORT))
        return (time.perf_counter() - t0) * 1000
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def probe_all(ips, log):
    """并发测速，返回 {ip: 毫秒}"""
    result = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(probe_ip, ip): ip for ip in ips}
        for fut in futures:
            ip = futures[fut]
            ms = fut.result()
            if ms is not None:
                result[ip] = ms
    if log:
        log("  测速完成：%d/%d 个 IP 可用" % (len(result), len(ips)))
    return result


# ---------------------------------------------------------------- IP 有效性校验
def http_check(ip, domain, path="/", timeout=5.0):
    """
    用真实 HTTPS 请求校验 ip 能否正常服务 domain。
    返回：2=正常，1=可达但被限流（403/429），0=不可用（证书错误/400/超时等）。
    只看 TCP 端口通不通是不够的：例如 api.github.com 的 IP 对 github.com
    的请求会直接返回 400，浏览器上就是 “Whoa there! You have sent an invalid request.”
    """
    sock = None
    try:
        sock = socket.create_connection((ip, 443), timeout=timeout)
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(sock, server_hostname=domain)
        try:
            req = (
                "GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: %s\r\n"
                "Accept: */*\r\nConnection: close\r\n\r\n" % (path, domain, USER_AGENT)
            )
            ss.sendall(req.encode())
            head = ss.recv(64).decode("iso-8859-1", "ignore")
        finally:
            ss.close()
        if not head.startswith("HTTP/"):
            return 0
        code = int(head.split()[1])
        if code in (200, 301, 302, 303, 304, 307, 308, 401, 404):
            return 2
        if code in (403, 429):
            return 1
        return 0
    except Exception:
        return 0
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def select_ip(domain, candidates, log=None):
    """按顺序校验候选 IP，返回第一个可用的（优先正常、其次被限流）。"""
    pool = DOMAIN_POOL[domain]
    path = VALIDATE_PATH.get(pool)
    if not candidates:
        return None
    if path is None:
        # raw 分组在测速阶段已确认 TCP 可达，直接取最快的
        return candidates[0]

    soft = None
    for ip in candidates[:4]:
        score = http_check(ip, domain, path)
        if score == 2:
            return ip
        if score == 1 and soft is None:
            soft = ip
    return soft


# ---------------------------------------------------------------- hosts 操作
def read_hosts(path=HOSTS_PATH):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def hosts_section_present(content):
    return BEGIN_MARK in content and END_MARK in content


def strip_section(content):
    pattern = re.compile(
        r"[ \t]*" + re.escape(BEGIN_MARK) + r".*?" + re.escape(END_MARK) + r"[^\n]*\n?",
        re.S,
    )
    content = pattern.sub("", content)
    return content.rstrip() + "\n"


def build_section(mapping):
    lines = [BEGIN_MARK]
    for domain in sorted(mapping):
        lines.append("%-16s %s" % (mapping[domain], domain))
    lines.append(END_MARK)
    return "\n".join(lines) + "\n"


def write_hosts(content, path=HOSTS_PATH):
    """写入 hosts；自动处理只读属性。"""
    if os.path.exists(path):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except Exception:
            pass
    normalized = content.replace("\r\n", "\n")
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(normalized)


def backup_hosts(path=HOSTS_PATH):
    if not os.path.exists(path):
        return None
    dst = "%s.bak.%s" % (path, time.strftime("%Y%m%d_%H%M%S"))
    try:
        shutil.copy2(path, dst)
        return dst
    except Exception:
        return None


# ---------------------------------------------------------------- 缓存 / 配置
def load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("mapping", {})
    except Exception:
        return {}


def save_cache(mapping):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "mapping": mapping},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_config():
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CONF_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------- 核心流程
def enable_accel(log, use_speedtest=True, hosts_path=HOSTS_PATH):
    """开启加速，返回写入的映射表。"""
    log("检查管理员权限 ...")
    if not is_admin():
        raise PermissionError("需要管理员权限才能修改 hosts，请右键“以管理员身份运行”")

    log("备份 hosts ...")
    bak = backup_hosts(hosts_path)
    log("  备份到：%s" % bak if bak else "  备份失败（不影响使用）")

    # 1. 收集候选 IP（按域名分别收集，避免 api 的 IP 被用到 github.com 上）
    candidates = {d: [] for d in DOMAIN_POOL}

    def _add(domain, ip):
        if ip not in candidates[domain]:
            candidates[domain].append(ip)

    cache = load_cache()
    if not use_speedtest:
        log("使用缓存 / 内置 IP 快速开启 ...")
        for domain, ip in cache.items():
            if domain in candidates:
                _add(domain, ip)

    if use_speedtest:
        log("获取在线 IP 列表 ...")
        online = fetch_online_ips(log)
        for domain, ips in online.items():
            for ip in ips:
                _add(domain, ip)

    for domain, pool_name in DOMAIN_POOL.items():
        for ip in BUILTIN_POOLS[pool_name]:
            _add(domain, ip)

    # 2. 测速排序（按域名各自的候选集）
    if use_speedtest:
        all_ips = sorted({ip for ips in candidates.values() for ip in ips})
        log("开始测速（共 %d 个 IP）..." % len(all_ips))
        latency = probe_all(all_ips, log)
        for domain in candidates:
            candidates[domain] = sorted(
                [ip for ip in candidates[domain] if ip in latency],
                key=lambda x: latency[x],
            )

    # 3. 真实 HTTPS 校验，剔除会返回 400 / 证书不匹配的 IP
    log("校验 IP 是否真的可用（HTTPS 实际请求）...")
    chosen = {}
    bad_ips = set()

    def _work(d):
        ip = select_ip(d, candidates.get(d, []))
        return d, ip

    domains = [d for d in DOMAIN_POOL if candidates.get(d)]
    with ThreadPoolExecutor(max_workers=10) as ex:
        for d, ip in ex.map(_work, domains):
            if ip:
                chosen[d] = ip
            else:
                bad_ips.update(candidates[d][:4])

    if bad_ips:
        log("  已自动跳过对相应域名不可用的 IP（共 %d 个）" % len(bad_ips))

    if chosen:
        log("优选结果：github.com -> %s，raw -> %s" % (
            chosen.get("github.com", "-"), chosen.get("raw.githubusercontent.com", "-")))
    if not use_speedtest and chosen:
        log("完成（未测速模式）")

    if not chosen:
        raise RuntimeError("没有可用 IP，请检查网络后重试")

    # 4. 写入 hosts
    log("写入 hosts ...")
    content = strip_section(read_hosts(hosts_path))
    write_hosts(content + build_section(chosen), hosts_path)

    log("刷新 DNS 缓存 ...")
    flush_dns()
    save_cache(chosen)

    ok, fail = len(chosen), len(DOMAIN_POOL) - len(chosen)
    log("完成：%d 个域名已加速%s" % (ok, ("，%d 个未找到可用 IP" % fail) if fail else ""))
    return chosen


def disable_accel(log, hosts_path=HOSTS_PATH):
    """关闭加速：删除 hosts 中的加速段。"""
    log("检查管理员权限 ...")
    if not is_admin():
        raise PermissionError("需要管理员权限才能修改 hosts，请右键“以管理员身份运行”")

    content = read_hosts(hosts_path)
    if not hosts_section_present(content):
        log("hosts 中没有加速记录，无需关闭")
        return False

    backup_hosts(hosts_path)
    write_hosts(strip_section(content), hosts_path)
    log("已从 hosts 移除加速记录")
    log("刷新 DNS 缓存 ...")
    flush_dns()
    log("关闭完成，已恢复系统默认 DNS 解析")
    return True


def status(hosts_path=HOSTS_PATH):
    try:
        return hosts_section_present(read_hosts(hosts_path))
    except Exception:
        return False


# ---------------------------------------------------------------- 图形界面
class App:
    BG = "#f5f6f8"
    CARD = "#ffffff"
    ACCENT = "#2f6fed"
    GREEN = "#1aa260"
    GRAY = "#8a8f98"
    TEXT = "#1f2329"

    def __init__(self, root):
        self.root = root
        self.busy = False
        self.uptodate = False

        root.title("%s v%s" % (APP_NAME, APP_VERSION))
        root.geometry("620x560")
        root.minsize(560, 480)
        root.configure(bg=self.BG)

        self._style()
        self._build()
        self.refresh_state()
        self.log("程序已启动。当前状态：%s" % ("已开启" if status() else "未开启"))
        if not is_admin():
            self.log("！当前不是管理员权限，无法修改 hosts")

    # -- 样式
    def _style(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("TFrame", background=self.BG)
        s.configure("TLabel", background=self.BG, foreground=self.TEXT,
                    font=("Microsoft YaHei UI", 10))
        s.configure("Card.TFrame", background=self.CARD)
        s.configure("Card.TLabel", background=self.CARD, foreground=self.TEXT,
                    font=("Microsoft YaHei UI", 10))
        s.configure("Title.TLabel", background=self.CARD, foreground=self.TEXT,
                    font=("Microsoft YaHei UI", 15, "bold"))
        s.configure("State.TLabel", background=self.CARD, foreground=self.GRAY,
                    font=("Microsoft YaHei UI", 11))
        s.configure("Hint.TLabel", background=self.BG, foreground=self.GRAY,
                    font=("Microsoft YaHei UI", 9))
        s.configure("Accent.TButton", font=("Microsoft YaHei UI", 11, "bold"),
                    padding=(16, 8))
        s.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 6))
        s.configure("TCheckbutton", background=self.BG, foreground=self.TEXT,
                    font=("Microsoft YaHei UI", 10))

    # -- 布局
    def _build(self):
        wrap = ttk.Frame(self.root, padding=16)
        wrap.pack(fill="both", expand=True)

        # 状态卡片
        card = ttk.Frame(wrap, style="Card.TFrame", padding=16)
        card.pack(fill="x")
        ttk.Label(card, text=APP_NAME, style="Title.TLabel").pack(anchor="w")

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(anchor="w", pady=(10, 0))
        self.dot = tk.Canvas(row, width=12, height=12, bg=self.CARD,
                             highlightthickness=0)
        self.dot.pack(side="left", pady=(2, 0))
        self.dot_item = self.dot.create_oval(1, 1, 11, 11, fill=self.GRAY, outline="")
        self.state_lbl = ttk.Label(row, text="检测中 ...", style="State.TLabel")
        self.state_lbl.pack(side="left", padx=(8, 0))

        self.admin_lbl = ttk.Label(card, text="", style="Card.TLabel")
        self.admin_lbl.pack(anchor="w", pady=(6, 0))

        # 按钮区
        btns = ttk.Frame(wrap)
        btns.pack(fill="x", pady=(14, 0))
        self.btn_on = ttk.Button(btns, text="开启加速", style="Accent.TButton",
                                 command=self.on_enable)
        self.btn_on.pack(side="left")
        self.btn_off = ttk.Button(btns, text="关闭加速", command=self.on_disable)
        self.btn_off.pack(side="left", padx=(10, 0))

        self.var_speed = tk.BooleanVar(value=True)
        chk = ttk.Checkbutton(wrap, text="开启前自动测速，优选最快的 IP（推荐；取消则用缓存快速开启）",
                              variable=self.var_speed)
        chk.pack(anchor="w", pady=(12, 0))

        ttk.Label(wrap, text="日志", style="Hint.TLabel").pack(anchor="w", pady=(12, 6))
        self.txt = ScrolledText(wrap, height=14, font=("Consolas", 9),
                                bg="#0f1115", fg="#d7dce3", insertbackground="#d7dce3",
                                relief="flat", borderwidth=0, wrap="word")
        self.txt.pack(fill="both", expand=True)
        self.txt.configure(state="disabled")

        foot = ttk.Frame(wrap)
        foot.pack(fill="x", pady=(8, 0))
        self.hint = ttk.Label(
            foot,
            text="加速范围：github.com / api / raw / gist / codeload / avatars 等；关闭后即时恢复默认解析",
            style="Hint.TLabel",
        )
        self.hint.pack(anchor="w")

    # -- 日志
    def log(self, msg):
        def _append():
            self.txt.configure(state="normal")
            self.txt.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
            self.txt.see("end")
            self.txt.configure(state="disabled")
        if threading.current_thread() is threading.main_thread():
            _append()
        else:
            self.root.after(0, _append)

    # -- 状态刷新
    def refresh_state(self):
        enabled = status()
        if enabled:
            self.dot.itemconfigure(self.dot_item, fill=self.GREEN)
            self.state_lbl.configure(text="加速已开启", foreground=self.GREEN)
            self.btn_on.state(["disabled"])
            self.btn_off.state(["!disabled"])
        else:
            self.dot.itemconfigure(self.dot_item, fill=self.GRAY)
            self.state_lbl.configure(text="加速未开启", foreground=self.GRAY)
            self.btn_on.state(["!disabled"])
            self.btn_off.state(["disabled"])

        if is_admin():
            self.admin_lbl.configure(text="权限：管理员（可修改 hosts）", foreground=self.GREEN)
        else:
            self.admin_lbl.configure(text="权限：普通用户（请以管理员身份运行）", foreground="#d9534f")

    def _set_busy(self, busy):
        self.busy = busy
        if busy:
            self.btn_on.state(["disabled"])
            self.btn_off.state(["disabled"])
            self.state_lbl.configure(text="处理中 ...", foreground=self.ACCENT)
            self.dot.itemconfigure(self.dot_item, fill=self.ACCENT)
        else:
            self.refresh_state()

    # -- 事件
    def on_enable(self):
        if self.busy:
            return
        if not is_admin():
            if messagebox.askyesno(APP_NAME, "修改 hosts 需要管理员权限，是否以管理员身份重启？"):
                if relaunch_as_admin():
                    self.root.destroy()
                else:
                    messagebox.showerror(APP_NAME, "提权失败，请手动右键 → 以管理员身份运行")
            return
        self._set_busy(True)
        threading.Thread(target=self._enable_worker,
                         args=(self.var_speed.get(),), daemon=True).start()

    def _enable_worker(self, speedtest):
        try:
            enable_accel(self.log, use_speedtest=speedtest)
            self.root.after(0, lambda: messagebox.showinfo(APP_NAME, "加速已开启，可打开 GitHub 试试。"))
        except Exception as e:
            self.log("失败：%s" % e)
            self.root.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
        finally:
            self.root.after(0, lambda: self._set_busy(False))

    def on_disable(self):
        if self.busy:
            return
        if not is_admin():
            messagebox.showwarning(APP_NAME, "需要管理员权限，请以管理员身份运行")
            return
        self._set_busy(True)
        threading.Thread(target=self._disable_worker, daemon=True).start()

    def _disable_worker(self):
        try:
            disable_accel(self.log)
            self.root.after(0, lambda: messagebox.showinfo(APP_NAME, "加速已关闭，已恢复系统默认解析。"))
        except Exception as e:
            self.log("失败：%s" % e)
            self.root.after(0, lambda: messagebox.showerror(APP_NAME, str(e)))
        finally:
            self.root.after(0, lambda: self._set_busy(False))


def main():
    if not is_admin() and "--no-elevate" not in sys.argv:
        # 打包为 EXE 时自带 UAC 清单；脚本方式运行时主动提权
        if not getattr(sys, "frozen", False) and relaunch_as_admin():
            return
    root = tk.Tk()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
