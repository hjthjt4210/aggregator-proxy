# 聚合代理 / Aggregator Proxy

把多个 OpenAI 兼容的上游（官方 API、Azure、各家中转站）聚合成**一个**本地端点，
对外提供 `Bearer` 鉴权的 `/v1/models` 与 `/v1/chat/completions`，
自带分组隔离、优先级调度、故障转移（failover）、失败冷却和 SSE 流式透传。

一个桌面窗口管完，不需要装 Docker、不需要数据库、不需要反向代理。

> **费用提示**：本工具会代你调用上游并按 token 计费。健康检查是**真实请求**，
> 每次探活都会产生费用。请不要在无人看管的情况下长期开启。
> 本项目只提供软件，不附带任何 API 额度。

---

## 功能

| | |
|---|---|
| **聚合密钥（分组）** | 每个分组一条随机密钥（`secrets.token_hex(16)`），客户端只看到这一条，上游密钥永不外泄 |
| **优先级调度** | 分组内每个模型可独立设优先级，数值越小越先尝试 |
| **故障转移** | 逐个候选上游重试，超时 / 连接失败 / 4xx / 5xx 自动切下一个 |
| **失败冷却** | 上游挂了就冷却 `cooldown_seconds` 秒，期间不参与调度，到期自动恢复 |
| **持续失败标记** | 认证类 4xx 判为持续性失败，不再无谓重试，需手动检查恢复 |
| **SSE 流式透传** | 客户端 `stream=true` 时逐 chunk 透传，不造假数据分片 |
| **字段保留** | `reasoning_content` / `reasoning` / `tool_calls` 原样保留，深度思考与函数调用不因转发而丢失 |
| **模型别名** | 固定模式下对外暴露自定义模型名，内部映射到真实上游模型 |
| **上游探活** | 手动检查单个上游或整个分组，展示延迟与完整错误原因 |
| **拉取模型列表** | 直接读上游 `/v1/models` 自动补全，也可手写 |
| **托盘 / 开机自启** | 关闭窗口驻留托盘；打包成 EXE 后可注册 Windows 开机自启 |
| **配置自愈** | 原子写入 + 自动备份，文件损坏时从 `.bak` 恢复 |

## 安装

### 方式一：下载 EXE（Windows）

到 [Releases](../../releases) 下载 `聚合代理.zip`，解压后运行 `聚合代理.exe`。
首次运行会在同级目录自动创建 `data/config.json` 与 `logs/`。

### 方式二：源码运行

需要 Python 3.10+：

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python app.py
```

命令行参数：

```bash
python app.py --startup    # 静默启动，只驻留托盘（开机自启用）
python app.py --selftest   # 打开窗口 2.5 秒后自动关闭并打印 SELFTEST_OK
python -m src.api_server   # 不起 GUI，单独跑 HTTP 服务便于调试
```

界面为中文，Windows 下开发与测试最充分。非 Windows 平台下"开机自启"会自动禁用，
其余功能未经系统验证。

## 快速上手

1. **概览**页确认服务已启动（默认 `127.0.0.1:5000`）。
2. **分组管理**页点「新增」，建一个分组，密钥留空即自动生成。
3. **上游管理**页选中该分组，添加上游：填中转站的接口地址（勾选自动补 `/v1`）、
   粘贴 `sk-` 密钥、拉取或手填模型、设优先级。
4. 点开分组信息条，复制**聚合地址**和**聚合密钥**给你的客户端。

### 客户端配置

```bash
curl http://127.0.0.1:5000/v1/chat/completions \
  -H "Authorization: Bearer <你的聚合密钥>" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"你好"}]}'
```

OpenAI Python SDK：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:5000/v1",
    api_key="你的聚合密钥",
)
```

任何支持自定义 `base_url` 的工具（LobeChat、Cherry Studio、Cline、one-api 下游、
各类 Agent 框架）都可以直接接入。

## 核心概念

**分组（Group）** —— 一条对外密钥及其可见的上游集合。给不同的人/项目发不同分组，
即可隔离可用模型与额度；删除分组会级联删除其下所有上游。

**上游（Upstream）** —— 一个 `base_url` + 一个密钥，其下可挂**多个模型**，
每个模型有自己的优先级和开关。同一个上游的多个模型共用一份密钥。

**两种模式**

- `固定` —— 对外只暴露你指定的模型别名（分组里的"对外模型名"），
  请求会按优先级顺序转发到分组内**所有**启用的上游模型。
  适合"给团队一个稳定模型名，背后随便换供应商"。
- `手动` —— 对外暴露真实模型名，客户端传什么模型就精确匹配到拥有该模型的上游；
  匹配不到时退回全量候选。适合"我自己清楚要调哪个模型"。

**调度链** —— 候选按 `priority` 升序排列，跳过冷却中与已判持续失败的上游，
逐个尝试直到成功；全部失败返回 `502`，并带上最后一个错误原因。

## 安全须知

请务必读完再用，这个工具的安全模型很简单，也很直接：

- **密钥以明文存储在 `data/config.json`。** 没有加密、没有系统钥匙串。
  请把该文件视同你的密码：不要提交到 Git（`.gitignore` 已默认排除 `data/`）、
  不要通过「导出配置」发给别人、不要把截图发到群里。
- **默认只监听 `127.0.0.1`。** 改成 `0.0.0.0` 会把一个能花你额度的服务开放给整个局域网，
  而**本项目当前没有任何限流、配额、频率或 IP 白名单机制** —— 谁拿到你的聚合密钥，
  谁就能无限制地消耗你的上游余额。只在可信网络下这样做。
- **不提供 HTTPS。** 需要对外服务请自行套一层带 TLS 的反向代理。
- **聚合密钥校验是明文比较**，且密钥永不过期、无法吊销（只能删除分组重建）。
- 日志刻意**不记录** API 密钥，但会记录上游名称、模型名与请求失败原因。

## 已知限制

- 仅实现 `/v1/models` 与 `/v1/chat/completions`。
  `embeddings`、`/v1/responses`、`images`、`audio` 等端点未实现，会返回 404。
- 没有用量统计：看不到每个分组花了多少 token、调了多少次。
- 没有速率限制与配额（见上）。
- 服务端口固定，被占用时不会自动换号，需要手动改设置并重启服务。
- 配置热更新需要重启服务生效。
- 单机单用户设计，配置文件不支持多实例并发写入。

以上任意一项你都需要，欢迎开 Issue 或提 PR。

## 项目结构

```
app.py                     入口：单实例锁、--startup / --selftest
src/
  main_window.py           主窗口、托盘、导航、全局样式
  api_server.py            FastAPI 应用、鉴权、调度、failover、冷却、流式转发
  config_manager.py        config.json 读写、原子保存、损坏恢复
  autostart.py             Windows 注册表开机自启
  logger.py                日志（约定：绝不写密钥）
  ui_widgets.py            通用控件
  pages/
    overview_page.py       概览
    groups_page.py         分组管理（聚合密钥）
    upstreams_page.py      上游管理
    logs_page.py           实时日志
    settings_page.py       设置
```

技术栈：Python + PySide6（界面）+ FastAPI / uvicorn（内嵌 HTTP 服务，跑在 Qt 子线程）
+ httpx（上游转发）+ PyInstaller（打包）。

### 打包

```bash
打包-发布版.bat        # 需要 .venv 内已装 pyinstaller
```

产出 `聚合代理.exe` 与 `_internal/`，配置与日志写在 EXE 同级目录。

## License

[MIT](LICENSE)
