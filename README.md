# lm-gateway

**Self-hosted OpenAI-compatible gateway that turns your gaming GPU into your private LLM API.**

把家里的游戏显卡变成你的专属大模型 API：本地跑 LM Studio / Ollama / [Strata](https://github.com/Niko1221/Strata) 加速引擎，通过 Cloudflare Tunnel 安全地暴露给全世界的设备调用 —— 不开放任何入站端口、不需要买任何 API。

> 笔者当前配置：一块 22GB 显存的 RTX 2080 Ti + 64GB 内存，跑 **Qwen3.8-Flash-Next 125B**（GSQ 量化）稳定 **42 tok/s**，256K 上下文。远程开发时它就是主力模型，**零 API 账单，只有电费**。

[中文](#功能) | [English](#english)

---

## 为什么需要它

家用宽带没有公网 IP，但你有三样东西：一块能跑大模型的显卡、一个 Cloudflare 账号、以及到处需要用 LLM 的设备（笔记本/手机/CI）。

`lm-gateway` 把它们连起来：

```
任意设备（笔记本 / 手机 / CI / Claude Code / Qoder）
        │  OpenAI 兼容 API + Bearer Key
        ▼
api.你的域名.com  ──Cloudflare 边缘 TLS──►  cloudflared 隧道（出站连接，不开端口）
        ▼
lm-gateway (127.0.0.1:8787)
  ├─ 鉴权      SHA-256 随机 key（256-bit 熵），按 key 限流 RPM
  ├─ 路由      按模型名分发到 lm-studio / ollama / strata 等上游
  ├─ 并发控制  全局 + 按 key，超限排队
  ├─ 计量      每条请求的 token / 延迟 / 速度 / 缓存命中 → SQLite
  └─ 计价      电费口径 / 等效 API 价 双模式
        ▼
本地推理引擎（不暴露公网）
  ├─ LM Studio   http://localhost:1234/v1
  ├─ Ollama      http://localhost:11434/v1
  └─ Strata      http://127.0.0.1:8080/v1   (125B 加速引擎)
```

## 功能

- **Windows 服务化**：网关、Cloudflare 隧道、Strata 加速器全部注册为系统服务，开机自启、崩溃自动拉起
- **PyQt6 图形控制台**：服务启停、实时指标（RPM / 延迟 / token / 缓存命中）、请求日志（含 tok/s）、按 key 的 token 用量与花费
- **模型热切换**：GUI 下拉框一键切换 Strata 挂载的量化档位（如 IQ2_XS ↔ IQ3_S），自动重启加速器
- **双口径计价**：
  - `电费口径` —— 按本机功耗和生成速度折算的边际电费（自己的机器就是个电费）
  - `等效 API 价` —— 按商用 API 牌价折算的等价成本（给别人用时的收费参考）
  - 按 fnmatch 通配符给每个模型路由单独定价
- **嵌入式 SSE 转发**：流式响应、usage 跨 chunk 解析、32MB 请求体上限、401 探测日志自动清理
- **一键密钥管理**：`sk-` + 32 字节随机 key，创建时仅展示一次，随时吊销；可设 RPM 限制

## 快速开始

### 0. 准备

- Windows 10/11（macOS/Linux 需自行调整服务脚本）
- Python 3.12+
- 一个 OpenAI 兼容的本地推理引擎（[LM Studio](https://lmstudio.ai/) / Ollama / [Strata](https://github.com/Niko1221/Strata) 任选）
- 一个 Cloudflare 账号 + 一个托管在 CF 的域名（可选，仅本机使用可不配）

### 1. 安装

```powershell
git clone https://github.com/<you>/lm-gateway.git
cd lm-gateway
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy config.example.json config.json   # 按需编辑
```

### 2. 运行

```powershell
python main.py                  # 不带参数 = 看总览状态
python main.py keys             # 创建第一个 API key（仅展示一次）
python main.py gui              # 图形控制台
```

注册为 Windows 服务（开机自启）：

```powershell
python scripts\gateway_svc.py install
```

Cloudflare 隧道（固定域名，需先在 CF 控制台创建 tunnel 并拿到 token）：

```powershell
# 全局装一次 cloudflared 后：
cloudflared service install <你的tunnel-token>
```

在 `config.json` 里填 `public_url`（如 `https://api.你的域名.com`），GUI 的"远程 API"地址和 `main.py` 状态检查都会自动使用它。

### 3. 远程调用

任何 OpenAI 兼容客户端：

```python
from openai import OpenAI
client = OpenAI(base_url="https://api.你的域名.com/v1", api_key="sk-你生成的key")
print(client.chat.completions.create(
    model="strata/qwen3.8-flash-next-iq3_s",
    messages=[{"role": "user", "content": "你好"}],
).choices[0].message.content)
```

Claude Code / Qoder 等工具同理，把 base URL 指过去即可（Anthropic 协议工具用环境变量）：

```powershell
$env:ANTHROPIC_BASE_URL = "https://api.你的域名.com"
```

### 4. 常用命令

| 命令 | 说明 |
|---|---|
| `python main.py` | 总览：服务状态、上游连通性、24h 用量 |
| `python main.py start / stop / restart` | 管理网关/隧道/加速器服务 |
| `python main.py usage` | 按 key 的 token 与费用报表 |
| `python main.py keys` | 密钥管理 |
| `python main.py gui` | 图形控制台 |

## 安全模型

与那些"把 agent 挂公网被扫"的事故不同，本网关**只暴露一个无状态推理代理**：

- 不开放任何入站端口（隧道为出站连接），源站 IP 藏在 Cloudflare 后面
- 无有效 key → 只能拿到 401；key 为 256-bit 随机数，爆破在数学上不可行
- 即使 key 泄露，攻击者能做的也只有"调用你的显卡"，接触不到文件系统/数据库/上游管理界面
- 每个设备发独立 key，泄露可单独吊销；按 key 设 RPM 限制可封顶损失

## Roadmap

- [ ] **一键部署**：`install.ps1` 一条命令完成 venv + 依赖 + 服务注册（其他人的部署方便化是下一阶段的核心方向）
- [ ] 跨平台服务脚本（Linux systemd / macOS launchd）
- [ ] Docker 镜像
- [ ] Web 控制台（远程管理 GUI）
- [ ] 按 key 的模型白名单 GUI 化

## English

**lm-gateway** is a self-hosted, OpenAI-compatible API gateway for local LLM runtimes (LM Studio, Ollama, or the [Strata](https://github.com/Niko1221/Strata) acceleration engine). It publishes your home GPU behind a Cloudflare Tunnel with zero inbound ports, bearer-key auth (256-bit entropy), per-key rate limiting, request metering (SQLite), and a PyQt6 console with dual-mode cost accounting — marginal electricity cost vs. equivalent commercial API price. Author's setup: Qwen3.8-Flash-Next 125B (GSQ quant) at 42 tok/s on a 22 GB RTX 2080 Ti, used daily as the primary model for remote coding tools. One-click installer for newcomers is the next milestone — see Roadmap.

## License

[MIT](LICENSE)
