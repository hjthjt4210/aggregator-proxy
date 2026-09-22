"""聚合代理 本地 API 服务（阶段三：流式 + 故障转移 + 冷却）。

对外提供 OpenAI 兼容接口：
  GET  /v1/models            -> 校验 Bearer 聚合 Key，返回可用模型列表
  POST /v1/chat/completions  -> 校验 Bearer 聚合 Key，转发到所选上游

阶段三要点：
  - 真实 SSE 流式转发（客户端 stream=true 完全透传上游 SSE，含 reasoning 分片）。
  - failover：按候选上游顺序依次尝试（优先模型匹配，再按优先级），失败自动切换。
  - 超时：每个上游可用独立 timeout（未配置则用全局 default_timeout）。
  - 冷却：某个上游失败后进入 cooldown_seconds 冷却期，期间不再被选中。
  - 保留 reasoning_content / reasoning / tool_calls 字段（非流式整体透传，
    流式逐 chunk 透传，天然保留分片）。
  - 不发假的数据分片；上游真实返回什么就转发什么。

候选池规则（沿用阶段二语义并扩展为有序链）：
  启用上游 -> 若存在与请求模型名匹配的上游，则只用这些；否则用全部。
  两者均按 priority（数值越小越优先）升序排列。
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from enum import IntEnum
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .config_manager import ConfigManager

logger = logging.getLogger("aggregator")


def get_lan_ip() -> str:
    """获取本机局域网 IP。失败时回退回环地址。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def api_display_host(cfg: dict) -> str:
    """对外展示的接入主机名：通配监听时给局域网 IP，绑定具体地址时如实显示。"""
    host = str(cfg.get("server", {}).get("host", "127.0.0.1")).strip()
    return get_lan_ip() if host in ("0.0.0.0", "::") else (host or "127.0.0.1")


# 健康状态只驻内存，避免把运行时状态写入含密钥的配置文件。
_health_lock = threading.RLock()
_health: dict[str, dict[str, Any]] = {}
# 转发失败冷却：key = "上游id::model"，value = 冷却结束时间戳（模块级，供 GUI 查询倒计时）
_cooldowns: dict[str, float] = {}


def get_cooldown_snapshot() -> dict[str, float]:
    """返回仍在冷却中的条目 {key: 结束时间戳}。"""
    now = time.time()
    return {key: expire for key, expire in _cooldowns.items() if expire > now}


def _cooldown_key(upstream_id: str, model_name: str | None = None) -> str:
    """冷却/调度的统一 key：上游 id + 具体模型名。"""
    return f"{upstream_id}::{model_name or ''}"


def is_in_cooldown(upstream_id: str, model_name: str | None = None) -> bool:
    return time.time() < _cooldowns.get(_cooldown_key(upstream_id, model_name), 0.0)


def sweep_expired_cooldowns() -> int:
    """清理过期冷却，并把瞬时失败（超时/断连/5xx）的健康状态恢复为未检查。

    持续性失败（如认证失败）保持"不可用"，需手动检查恢复。返回恢复的条数。
    """
    now = time.time()
    recovered = 0
    for key, expire in list(_cooldowns.items()):
        if expire > now:
            continue
        _cooldowns.pop(key, None)
        uid, _, model = key.partition("::")
        entry = _health.get(key)
        if entry and entry.get("status") == "unhealthy" and entry.get("transient", False):
            _set_health(uid, "unknown", model_name=model or None)
            recovered += 1
    return recovered


def _transient_by_status(status_code: int | None) -> bool:
    """按 HTTP 状态码判断是否瞬时失败：瞬时失败冷却后自动重试。

    4xx（除 408/429）多为认证/资源问题，重试无用 → 不可用；
    超时、连接失败、408/429、5xx → 可能下次就通 → 冷却。
    """
    if status_code is None:
        return True
    if status_code in (408, 429):
        return True
    if 400 <= status_code < 500:
        return False
    return True


def _health_key(upstream_id: str, model_name: str | None = None) -> str:
    return f"{upstream_id}::{model_name}" if model_name else upstream_id


def get_health(upstream_id: str, model_name: str | None = None) -> dict[str, Any]:
    with _health_lock:
        return dict(_health.get(_health_key(upstream_id, model_name), {
            "status": "unknown",
            "latency_ms": None,
            "error": "",
            "checked_at": None,
            "transient": False,
        }))


def _set_health(upstream_id: str, status: str, latency_ms: int | None = None, error: str = "", model_name: str | None = None, transient: bool = False) -> None:
    if not upstream_id:
        return
    with _health_lock:
        key = _health_key(upstream_id, model_name)
        previous = _health.get(key, {})
        checked_at = previous.get("checked_at") if status == "checking" else time.time()
        value = {
            "status": status,
            "latency_ms": latency_ms,
            "error": error[:300],
            "checked_at": checked_at,
            "transient": bool(transient),
        }
        _health[key] = value
        if model_name:
            _health[upstream_id] = value


def _health_check(upstream: dict, timeout: float, model_name: str | None = None) -> dict[str, Any]:
    """发送最小对话请求，按模型判断上游可用性并保留完整原因。

    返回含 transient 字段：True=瞬时失败（超时/断连/5xx，冷却后自动重试）；
    False=持续性失败（4xx 认证/资源问题，标记"不可用"直到手动检查）。
    """
    started = time.perf_counter()
    models = _model_entries(upstream)
    model_name = model_name or (models[0].get("name") if models else upstream.get("model"))
    target = str(upstream.get("base_url", "")).rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {upstream.get('api_key', '')}"}
    payload = {"model": model_name, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1, "stream": False}
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(target, headers=headers, json=payload)
            latency = int((time.perf_counter() - started) * 1000)
            if 200 <= response.status_code < 300:
                return {"status": "healthy", "latency_ms": latency, "error": "", "transient": False}
            detail = response.text.strip().replace("\\n", " ")[:220]
            return {
                "status": "unhealthy",
                "latency_ms": latency,
                "error": f"HTTP {response.status_code}" + (f"：{detail}" if detail else ""),
                "transient": _transient_by_status(response.status_code),
            }
    except httpx.TimeoutException:
        latency = int((time.perf_counter() - started) * 1000)
        return {"status": "unhealthy", "latency_ms": latency, "error": f"请求超时（{timeout:g}秒）", "transient": True}
    except httpx.ConnectError as exc:
        return {"status": "unhealthy", "latency_ms": None, "error": f"无法连接上游：{str(exc)[:180]}", "transient": True}
    except httpx.HTTPError as exc:
        return {"status": "unhealthy", "latency_ms": None, "error": f"网络请求失败：{str(exc)[:180]}", "transient": True}


def check_upstream_now(upstream: dict, cfg: dict, model_name: str | None = None) -> dict[str, Any]:
    """供 GUI 手动检查；每个模型发送一次最小对话请求。

    检查失败若是瞬时错误，同样进入冷却（到期自动恢复参与调度）。
    """
    uid = str(upstream.get("id", ""))
    _set_health(uid, "checking", model_name=model_name)
    result = _health_check(upstream, min(_upstream_timeout(upstream, cfg), 15.0), model_name)
    _set_health(uid, **result, model_name=model_name)
    if result.get("status") != "healthy" and result.get("transient", False):
        try:
            seconds = float(cfg.get("server", {}).get("cooldown_seconds", 120))
        except (TypeError, ValueError):
            seconds = 120.0
        _cooldowns[_cooldown_key(uid, model_name)] = time.time() + seconds
    return result


def check_group_now(upstreams: list[dict], cfg: dict, group_id: str) -> list[dict[str, Any]]:
    results = []
    for upstream in upstreams:
        if upstream.get("group_id") != group_id or not upstream.get("enabled", True):
            continue
        for model in _model_entries(upstream):
            if model.get("enabled", True):
                results.append(check_upstream_now(upstream, cfg, str(model.get("name"))))
    return results


# ---------------------------------------------------------------- 线程包装
class _UvicornThread(QThread):
    """在子线程里运行 uvicorn.Server，支持从主线程请求优雅停止。"""

    failed = Signal(str)  # 启动失败原因（如端口被占用）

    def __init__(self, app: FastAPI, host: str, port: int):
        super().__init__()
        self._app = app
        self._host = host
        self._port = port
        self._server: Any = None
        self.port_occupied = False  # 端口占用属配置问题，不该走自动重试

    def run(self) -> None:  # QThread 入口，运行直到 stopped
        try:
            import uvicorn

            config = uvicorn.Config(
                self._app,
                host=self._host,
                port=self._port,
                log_level="warning",
                access_log=False,
                log_config=None,  # 统一走 aggregator 日志，避免 uvicorn 自带格式
            )
            self._server = uvicorn.Server(config)
            self._server.run()  # 阻塞直到 should_exit=True
        except SystemExit:
            # uvicorn 绑定端口失败时会 sys.exit(1)；在 QThread 里必须拦住，
            # 否则 SystemExit 会把整个 GUI 进程一起带崩。
            self.port_occupied = True
            message = f"端口 {self._port} 被占用"
            logger.error("API 服务启动失败：%s，请修改监听端口", message)
            self.failed.emit(message)
        except Exception as exc:  # noqa: BLE001
            logger.exception("API 服务线程异常退出: %s", exc)
            self.failed.emit(str(exc))
        finally:
            self._server = None

    def request_stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


class ApiServerController(QObject):
    """管理 API 服务的启动 / 停止，并通过信号把状态与日志回传 GUI。"""

    state_changed = Signal(bool)  # True=运行中, False=已停止
    log_message = Signal(str)
    start_failed = Signal(str)  # 启动失败原因（端口占用等）

    _MAX_RETRIES = 5
    _RETRY_DELAY_MS = 5000

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.cfg = config_manager
        self._thread: _UvicornThread | None = None
        self._running = False
        self._stopping = False
        self._pending_restart = False
        self._retries = 0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def current_port(self) -> int | None:
        cfg = self.cfg.load()
        return int(cfg.get("server", {}).get("port", 5000))

    # ---------------------------------------------------------- 启停
    def start(self) -> bool:
        """使用配置里的固定端口启动；端口被占不换号，报错提示用户处理。

        端口稳定优先：agent/客户端里写好的地址不能因避让漂移而失效。
        """
        if self._running:
            return False
        cfg = self.cfg.load()
        server = cfg.get("server", {})
        host = str(server.get("host", "127.0.0.1"))
        port = int(server.get("port", 5000))
        return self._launch(host, port)

    def _launch(self, host: str, port: int) -> bool:
        app = create_app(self.cfg)
        self._thread = _UvicornThread(app, host, port)
        self._thread.failed.connect(self.start_failed)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()
        self._running = True
        self._stopping = False
        self._retries = 0
        logger.info("API 服务启动：http://%s:%s", host, port)
        self.log_message.emit(f"API 服务已启动 http://{host}:{port}")
        self.state_changed.emit(True)
        return True

    def stop(self) -> bool:
        if not self._running or self._thread is None:
            return False
        logger.info("正在停止 API 服务……")
        self._stopping = True
        self._thread.request_stop()
        # 状态由 _on_thread_finished 统一置回，避免在真正退出前显示已停止
        return True

    def restart(self) -> bool:
        if self._running:
            # 等线程真正退出后再由 _on_thread_finished 触发启动，避免端口抢用
            self._pending_restart = True
            self.stop()
            return True
        return self.start()

    def shutdown(self) -> None:
        """应用关闭时调用：请求停止并等待线程正常退出。"""
        if not self._running or self._thread is None:
            return
        self._stopping = True
        self._thread.request_stop()
        self._thread.wait(3000)
        self._running = False

    def _on_thread_finished(self) -> None:
        was_running = self._running
        self._running = False
        port_occupied = bool(getattr(self._thread, "port_occupied", False)) if self._thread else False
        if self._pending_restart:
            self._pending_restart = False
            self.start()
            return
        if not was_running:
            return
        if self._stopping:
            self._stopping = False
            logger.info("API 服务已停止")
            self.log_message.emit("API 服务已停止")
            self.state_changed.emit(False)
            return
        if port_occupied:
            # 端口被占用是配置问题，重试不会有结果，保持停止状态等用户处理
            logger.error("API 服务启动失败：端口被占用，已停止自动重试")
            self.state_changed.emit(False)
            return
        # 非主动停止：同端口延迟自动重启（端口保持不变）
        self._retry_after_failure()

    def _retry_after_failure(self) -> None:
        if self._retries >= self._MAX_RETRIES:
            self._retries = 0
            self.start_failed.emit("服务反复异常退出，已停止重试")
            self.state_changed.emit(False)
            return
        self._retries += 1
        cfg = self.cfg.load()
        host = str(cfg.get("server", {}).get("host", "127.0.0.1"))
        port = int(cfg.get("server", {}).get("port", 5000))
        delay = self._RETRY_DELAY_MS * self._retries
        logger.warning("API 服务异常退出，%d 秒后在端口 %d 重试（第 %d 次）", delay // 1000, port, self._retries)
        self.log_message.emit(f"服务异常退出，{delay // 1000} 秒后自动重启（端口不变）")
        QTimer.singleShot(delay, lambda: self._launch(host, port))


# ---------------------------------------------------------------- 业务逻辑
def _resolve_group(cfg: dict, api_key: str) -> dict | None:
    for g in cfg.get("groups", []):
        if g.get("api_key") == api_key:
            return g
    return None


def _authenticate(cfg: dict, request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        api_key = auth[7:].strip()
    else:
        api_key = ""
    group = _resolve_group(cfg, api_key)
    if group is None:
        raise HTTPException(status_code=401, detail="无效的聚合 API Key")
    return group


def _sort_key(u: dict):
    p = u.get("priority", 9999)
    return (int(p) if isinstance(p, (int, float)) else 9999, str(u.get("name", "")))


def _record_forward_result(up: dict, status: str, latency_ms: int | None, error: str = "", transient: bool = False) -> None:
    _set_health(str(up.get("id", "")), status, latency_ms, error, model_name=str(up.get("model") or ""), transient=transient)


def reorder_group_models(
    upstreams: list[dict],
    group_id: str,
    target_upstream_id: str | None = None,
    incoming_models: list[dict] | None = None,
) -> None:
    """按插入规则整理分组内模型优先级，结果为连续的 1..N。"""
    entries: list[tuple[dict, dict]] = []
    target = None
    for upstream in upstreams:
        if str(upstream.get("group_id")) != str(group_id):
            continue
        if target_upstream_id and str(upstream.get("id")) == str(target_upstream_id):
            target = upstream
            if incoming_models is not None:
                continue
        for model in _model_entries(upstream):
            entries.append((upstream, dict(model)))
    entries.sort(key=lambda item: (int(item[1].get("priority") or 9999), str(item[1].get("name", ""))))
    if target is not None and incoming_models is not None:
        for model in sorted(incoming_models, key=lambda item: int(item.get("priority") or 1)):
            copied = dict(model)
            index = max(0, min(len(entries), int(copied.get("priority") or 1) - 1))
            entries.insert(index, (target, copied))
    grouped: dict[str, list[dict]] = {}
    for priority, (upstream, model) in enumerate(entries, 1):
        model["priority"] = priority
        model.pop("external_name", None)
        grouped.setdefault(str(upstream.get("id")), []).append(model)
    for upstream in upstreams:
        if str(upstream.get("group_id")) != str(group_id):
            continue
        upstream["models"] = grouped.get(str(upstream.get("id")), [])
        upstream.pop("model", None)


def _model_entries(upstream: dict) -> list[dict]:
    """读取新 models 数组，并兼容旧版 model/priority 字段。"""
    models = upstream.get("models")
    if isinstance(models, list) and models:
        return [m for m in models if isinstance(m, dict) and m.get("name")]
    model = str(upstream.get("model", "")).strip()
    return [{
        "name": model,
        "external_name": model,
        "priority": upstream.get("priority", 1),
        "enabled": upstream.get("enabled", True),
    }] if model else []


def _candidate_upstreams(cfg: dict, model_name: str, group: dict | None = None) -> list[dict]:
    """展开分组内启用模型。固定模式按优先级全量转发；手动模式按真实模型名匹配。

    跳过两类：持续性失败（4xx 认证/资源问题，需手动检查恢复）和冷却中的
    （瞬时失败后冷却，到期自动恢复"未检查"并重新参与调度）。
    """
    group = group or {}
    group_id = str(group.get("id", ""))
    mode = str(group.get("mode") or "fixed")
    candidates = []
    for upstream in cfg.get("upstreams", []):
        if upstream.get("group_id") != group_id or not upstream.get("enabled", True):
            continue
        for model in _model_entries(upstream):
            if not model.get("enabled", True):
                continue
            model_name_real = str(model.get("name", ""))
            uid = str(upstream.get("id", ""))
            health = get_health(uid, model_name_real)
            if health.get("status") == "unhealthy" and not health.get("transient", False):
                continue  # 持续性失败：重试无意义，等手动检查
            if is_in_cooldown(uid, model_name_real):
                continue  # 瞬时失败冷却中：到期自动恢复
            candidate = dict(upstream)
            candidate["model"] = model_name_real
            candidate["priority"] = model.get("priority", 9999)
            candidates.append(candidate)
    if mode == "fixed":
        return sorted(candidates, key=_sort_key)
    matched = [u for u in candidates if u.get("model") == model_name]
    return sorted(matched or candidates, key=_sort_key)


def _upstream_timeout(up: dict, cfg: dict) -> float:
    t = up.get("timeout")
    if isinstance(t, (int, float)) and t > 0:
        return float(t)
    try:
        return float(cfg.get("server", {}).get("default_timeout", 120))
    except (TypeError, ValueError):
        return 120.0


def _build_target(up: dict) -> str:
    base = str(up.get("base_url", "")).rstrip("/")
    return f"{base}/chat/completions"


def _build_payload(up: dict, body: dict) -> dict:
    payload = dict(body)
    up_model = up.get("model")
    if up_model and up_model != payload.get("model"):
        payload["model"] = up_model
    return payload


class _FailoverContext:
    """承载一次请求的 failover/冷却运行时状态。"""

    def __init__(self, cfg: dict, cooldowns: dict[str, float]):
        self.cfg = cfg
        self.cooldowns = cooldowns

    def _key(self, up: dict) -> str:
        return f"{up.get('id')}::{up.get('model') or ''}"

    def in_cooldown(self, up: dict) -> bool:
        key = self._key(up)
        if not up.get("id"):
            return False
        return time.time() < self.cooldowns.get(key, 0.0)

    def mark_cooldown(self, up: dict) -> None:
        if not up.get("id"):
            return
        try:
            seconds = float(self.cfg.get("server", {}).get("cooldown_seconds", 120))
        except (TypeError, ValueError):
            seconds = 120.0
        self.cooldowns[self._key(up)] = time.time() + seconds
        logger.debug("上游[%s/%s] 进入冷却 %.0fs", up.get("name"), up.get("model"), seconds)


async def _try_start_stream(client: httpx.AsyncClient, up: dict, body: dict):
    """发送流式请求，返回 (response_or_None, error_str_or_None)。不抛异常。"""
    target = _build_target(up)
    payload = _build_payload(up, body)
    headers = {
        "Authorization": f"Bearer {up.get('api_key', '')}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    request = client.build_request("POST", target, json=payload, headers=headers)
    try:
        resp = await client.send(request, stream=True)  # type: ignore[misc]
    except httpx.HTTPError as exc:
        return None, f"连接失败: {exc}"
    if resp.status_code != 200:
        return None, f"上游返回 {resp.status_code}"
    return resp, None


# ---------------------------------------------------------------- FastAPI app
def create_app(config_manager: ConfigManager) -> FastAPI:
    app = FastAPI(title="聚合代理", version="1.2.0")

    # 运行时冷却状态，仅驻内存，不写入 config（config 含 API Key 敏感信息）。
    # 使用模块级 _cooldowns：GUI 可查询倒计时；重启服务后时间戳过期的条目自然失效。
    cooldowns: dict[str, float] = _cooldowns

    @app.get("/v1/models")
    async def list_models(request: Request):
        cfg = config_manager.load()
        group = _authenticate(cfg, request)
        group_id = str(group.get("id", ""))

        models: set[str] = set()
        if str(group.get("mode") or "fixed") == "fixed":
            ext = group.get("external_models") or []
            if isinstance(ext, str):
                names = [ext]
            else:
                names = [str(x) for x in ext]
            models.update(x.strip() for x in names if str(x).strip())
            if not models:
                models.add(str(group.get("name") or "default"))
        else:
            for u in cfg.get("upstreams", []):
                if u.get("group_id") != group_id or not u.get("enabled", True):
                    continue
                for model in _model_entries(u):
                    if model.get("enabled", True) and model.get("name"):
                        models.add(str(model["name"]))

        data = [
            {
                "id": m,
                "object": "model",
                "created": 0,
                "owned_by": "aggregator",
            }
            for m in sorted(models)
        ]
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        cfg = config_manager.load()
        group = _authenticate(cfg, request)

        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="请求体不是合法 JSON")

        model = str(body.get("model", ""))
        want_stream = bool(body.get("stream"))

        candidates = _candidate_upstreams(cfg, model, group)
        if not candidates:
            raise HTTPException(status_code=503, detail="没有可用的上游")

        fc = _FailoverContext(cfg, cooldowns)

        if want_stream:
            return await _handle_stream(fc, candidates, body)
        return await _handle_plain(fc, candidates, body)

    async def _handle_plain(fc: _FailoverContext, candidates: list[dict], body: dict) -> JSONResponse:
        """非流式：按候选顺序 failover，冷却跳过，成功返回完整 JSON。"""
        last_error = "无可用候选"
        seen = 0
        for up in candidates:
            if fc.in_cooldown(up):
                continue
            seen += 1
            target = _build_target(up)
            payload = _build_payload(up, body)
            headers = {
                "Authorization": f"Bearer {up.get('api_key', '')}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            timeout = _upstream_timeout(up, fc.cfg)
            logger.info("转发(非流式) -> 上游[%s] 模型=%s", up.get("name"), up.get("model") or payload.get("model"))
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(target, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"上游[{up.get('name')}] 连接失败: {exc}"
                logger.warning("%s", last_error)
                _record_forward_result(up, "unhealthy", None, last_error, transient=True)
                fc.mark_cooldown(up)
                continue
            if resp.status_code != 200:
                last_error = f"上游[{up.get('name')}] 返回 {resp.status_code}: {resp.text[:200]}"
                logger.warning("%s", last_error)
                _record_forward_result(
                    up, "unhealthy", None, last_error,
                    transient=_transient_by_status(resp.status_code),
                )
                fc.mark_cooldown(up)
                continue
            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text}
            _record_forward_result(up, "healthy", None, "")
            return JSONResponse(content=data, status_code=200)

        raise HTTPException(status_code=502, detail=f"所有上游均失败: {last_error}")

    async def _handle_stream(fc: _FailoverContext, candidates: list[dict], body: dict) -> StreamingResponse:
        """流式：逐个候选发送流式请求；拿到 200 响应头即锁定该上游，并逐块透传 SSE。"""
        accepted: tuple[httpx.AsyncClient, httpx.Response] | None = None
        last_error = "无可用候选"
        for up in candidates:
            if fc.in_cooldown(up):
                continue
            timeout = _upstream_timeout(up, fc.cfg)
            payload = _build_payload(up, body)
            logger.info("转发(流式) -> 上游[%s] 模型=%s", up.get("name"), payload.get("model"))
            client = httpx.AsyncClient(timeout=timeout)
            try:
                resp, err = await _try_start_stream(client, up, body)
            except Exception as exc:  # noqa: BLE001
                resp, err = None, str(exc)
            if resp is None:
                last_error = f"上游[{up.get('name')}] {err}"
                logger.warning("%s", last_error)
                _record_forward_result(up, "unhealthy", None, last_error, transient=True)
                fc.mark_cooldown(up)
                await client.aclose()
                continue
            _record_forward_result(up, "healthy", None, "")
            accepted = (client, resp)
            break

        if accepted is None:
            raise HTTPException(status_code=502, detail=f"所有上游均失败: {last_error}")

        client, resp = accepted

        async def stream_gen():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                try:
                    await resp.aclose()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001
                    pass

        response = StreamingResponse(
            stream_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # 供可能存在的反向代理禁用缓冲，避免 SSE 被缓存
                "X-Accel-Buffering": "no",
            },
        )
        return response

    return app


if __name__ == "__main__":
    # 方便独立调试：python -m src.api_server
    import uvicorn

    cm = ConfigManager()
    srv = cm.load()["server"]
    uvicorn.run(create_app(cm), host=str(srv.get("host", "127.0.0.1")), port=int(srv.get("port", 5000)))
