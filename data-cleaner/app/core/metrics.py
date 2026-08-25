"""进程内指标（可观测性，Phase 4）

轻量实现：进程内计数器/直方图，无需额外依赖。/metrics 端点暴露 Prometheus 风格文本，
便于 Grafana/Prometheus 抓取。多进程部署时建议接 prometheus-client（此处保持零外部依赖）。
"""
import threading
import time

_lock = threading.Lock()
_counters: dict[str, int] = {
    "pipeline_runs_total": 0,
    "pipeline_runs_failed_total": 0,
    "factor_compute_total": 0,
    "http_errors_total": 0,
}
_histograms: dict[str, list] = {
    "pipeline_duration_seconds": [],
    "factor_compute_seconds": [],
}
_gauge: dict[str, float] = {
    "factors_registered": 0.0,
    "last_rows_out": 0.0,
    "uptime_start_ts": time.time(),
}


def inc(name: str, by: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + by


def observe(name: str, value: float) -> None:
    with _lock:
        _histograms.setdefault(name, []).append(value)
        if len(_histograms[name]) > 1000:
            _histograms[name] = _histograms[name][-1000:]


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauge[name] = value


def _quantiles(values: list, qs=(0.5, 0.9, 0.99)):
    if not values:
        return {f"p{int(q*100)}": 0.0 for q in qs}
    s = sorted(values)
    return {f"p{int(q*100)}": s[min(len(s) - 1, int(q * len(s))) - 1 + 1 - 1] for q in qs}


def render() -> str:
    """渲染 Prometheus 风格文本"""
    with _lock:
        lines = []
        for k, v in _counters.items():
            lines.append(f"# TYPE {k} counter")
            lines.append(f"{k} {v}")
        for k, v in _gauge.items():
            lines.append(f"# TYPE {k} gauge")
            lines.append(f"{k} {v}")
        for k, vals in _histograms.items():
            lines.append(f"# TYPE {k} histogram")
            if vals:
                lines.append(f"{k}_count {len(vals)}")
                lines.append(f"{k}_sum {sum(vals):.4f}")
                for qk, qv in _quantiles(vals).items():
                    lines.append(f"{k}_{qk} {qv:.4f}")
        return "\n".join(lines) + "\n"


def record_pipeline(start_ts: float, ok: bool, rows_out: int) -> None:
    inc("pipeline_runs_total")
    if not ok:
        inc("pipeline_runs_failed_total")
    else:
        set_gauge("last_rows_out", float(rows_out))
    observe("pipeline_duration_seconds", time.time() - start_ts)
