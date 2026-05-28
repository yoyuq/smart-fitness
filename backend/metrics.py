"""
F-06 监控: Prometheus exposition + 关键指标

为了不引入 prometheus_client 依赖，自己实现 Prometheus 文本格式输出。
覆盖: HTTP 请求数/耗时, vision/infer 计数与延迟, 推理失败计数, DB 行数,
      WebSocket 在线数, MQTT 接入计数。

用法:
    from metrics import metrics, MetricsMiddleware, prometheus_text
    app.add_middleware(MetricsMiddleware)

    @app.get("/metrics")
    async def get_metrics():
        return Response(content=prometheus_text(), media_type="text/plain; version=0.0.4")
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple


class _Histogram:
    """简化直方图: 固定 bucket，记录 count/sum/per-bucket。"""

    DEFAULT_BUCKETS_SEC = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self, buckets: Tuple[float, ...] = DEFAULT_BUCKETS_SEC):
        self.buckets = buckets
        self.counts: Dict[float, int] = {b: 0 for b in buckets}
        self.inf_count = 0
        self.total_count = 0
        self.total_sum = 0.0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self.total_count += 1
            self.total_sum += float(value)
            # 只在最小匹配的 bucket +1，render 时交叠递增
            for b in self.buckets:
                if value <= b:
                    self.counts[b] += 1
                    break
            self.inf_count += 1

    def render(self, name: str, help_text: str, labels: Dict[str, str] | None = None) -> str:
        lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
        lbl = _fmt_labels(labels)
        cumulative = 0
        for b in self.buckets:
            cumulative += self.counts[b]
            lb = _merge_labels(labels, {"le": _fmt_float(b)})
            lines.append(f"{name}_bucket{_fmt_labels(lb)} {cumulative}")
        lb = _merge_labels(labels, {"le": "+Inf"})
        lines.append(f"{name}_bucket{_fmt_labels(lb)} {self.inf_count}")
        lines.append(f"{name}_count{lbl} {self.total_count}")
        lines.append(f"{name}_sum{lbl} {self.total_sum:.6f}")
        return "\n".join(lines)


def _fmt_float(v: float) -> str:
    if v == int(v):
        return f"{int(v)}"
    return f"{v:g}"


def _fmt_labels(labels: Dict[str, str] | None) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape(v)}"' for k, v in labels.items()]
    return "{" + ",".join(parts) + "}"


def _escape(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")


def _merge_labels(a: Dict[str, str] | None, b: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if a:
        out.update(a)
    out.update(b)
    return out


class Metrics:
    """全局指标容器。线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 计数器: 按 (name, labels-tuple) 累积
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = defaultdict(float)
        # gauge
        self._gauges: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
        # 直方图: name -> {label-tuple -> Histogram}
        self._hist: Dict[str, Dict[Tuple[Tuple[str, str], ...], _Histogram]] = defaultdict(dict)
        self._counter_help: Dict[str, str] = {}
        self._gauge_help: Dict[str, str] = {}
        self._hist_help: Dict[str, str] = {}

        # 进程信息
        self.process_start_time = time.time()

    def inc(self, name: str, value: float = 1.0, labels: Dict[str, str] | None = None,
            help_text: str = "") -> None:
        key = (name, _labels_to_tuple(labels))
        with self._lock:
            self._counters[key] += value
            if help_text and name not in self._counter_help:
                self._counter_help[name] = help_text

    def set_gauge(self, name: str, value: float, labels: Dict[str, str] | None = None,
                  help_text: str = "") -> None:
        key = (name, _labels_to_tuple(labels))
        with self._lock:
            self._gauges[key] = float(value)
            if help_text and name not in self._gauge_help:
                self._gauge_help[name] = help_text

    def observe(self, name: str, value: float, labels: Dict[str, str] | None = None,
                help_text: str = "") -> None:
        lbl_tuple = _labels_to_tuple(labels)
        with self._lock:
            bucket_map = self._hist[name]
            hist = bucket_map.get(lbl_tuple)
            if hist is None:
                hist = _Histogram()
                bucket_map[lbl_tuple] = hist
            if help_text and name not in self._hist_help:
                self._hist_help[name] = help_text
        hist.observe(value)  # 锁在 hist 内部

    def render(self) -> str:
        lines: List[str] = []
        with self._lock:
            counters = list(self._counters.items())
            gauges = list(self._gauges.items())
            hist_snapshot = {n: list(d.items()) for n, d in self._hist.items()}
            c_help = dict(self._counter_help)
            g_help = dict(self._gauge_help)
            h_help = dict(self._hist_help)

        # counters
        seen = set()
        for (name, lbl_tuple), value in counters:
            if name not in seen:
                lines.append(f"# HELP {name} {c_help.get(name, '')}")
                lines.append(f"# TYPE {name} counter")
                seen.add(name)
            lines.append(f"{name}{_fmt_labels(dict(lbl_tuple))} {value:.6f}")

        # gauges
        seen = set()
        for (name, lbl_tuple), value in gauges:
            if name not in seen:
                lines.append(f"# HELP {name} {g_help.get(name, '')}")
                lines.append(f"# TYPE {name} gauge")
                seen.add(name)
            lines.append(f"{name}{_fmt_labels(dict(lbl_tuple))} {value:.6f}")

        # histograms
        for name, items in hist_snapshot.items():
            for lbl_tuple, hist in items:
                lines.append(hist.render(name, h_help.get(name, ""), dict(lbl_tuple)))

        # 内置 process 指标
        lines.append(f"# HELP process_start_time_seconds Unix timestamp of process start")
        lines.append(f"# TYPE process_start_time_seconds gauge")
        lines.append(f"process_start_time_seconds {self.process_start_time:.6f}")
        lines.append(f"# HELP process_uptime_seconds Process uptime in seconds")
        lines.append(f"# TYPE process_uptime_seconds gauge")
        lines.append(f"process_uptime_seconds {time.time() - self.process_start_time:.6f}")

        return "\n".join(lines) + "\n"


def _labels_to_tuple(labels: Dict[str, str] | None) -> Tuple[Tuple[str, str], ...]:
    if not labels:
        return tuple()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


# 全局单例
metrics = Metrics()


def prometheus_text() -> str:
    return metrics.render()


# ---------- FastAPI 中间件 ----------

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as _Request
except ImportError:  # 仅在直接 import 用例
    BaseHTTPMiddleware = object  # type: ignore
    _Request = None  # type: ignore


class MetricsMiddleware(BaseHTTPMiddleware):  # type: ignore[misc]
    """记录 HTTP 请求数与耗时。"""

    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        path = request.url.path
        # 跳过 metrics 自身
        if path == "/metrics":
            return await call_next(request)
        method = request.method
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        except Exception:
            metrics.inc("fitness_http_exceptions_total", 1.0,
                        labels={"method": method, "path": path},
                        help_text="HTTP 5xx 异常计数")
            raise
        finally:
            elapsed = time.perf_counter() - start
            metrics.inc("fitness_http_requests_total", 1.0,
                        labels={"method": method, "path": path, "status": status},
                        help_text="HTTP 请求计数")
            metrics.observe("fitness_http_request_duration_seconds", elapsed,
                            labels={"method": method, "path": path},
                            help_text="HTTP 请求耗时（秒）")
