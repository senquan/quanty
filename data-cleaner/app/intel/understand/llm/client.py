"""LLM 客户端（P1-3）：OpenAI 兼容 chat.completions + 预算闸 + llm_runs 审计

- 零新依赖：httpx 直调（deepseek/qwen/openai/moonshot 的 OpenAI 兼容端点通用，
  本地 ollama 同一协议——P3 切本地不用改调用方）。
- 重试：网络/5xx/429 最多 3 次退避重试；schema 不合规的重试在 service 层（带错误回喂）。
- **预算闸**：调用前查 llm_runs 当日 sum(cost_cny)，超 INTEL_DAILY_BUDGET_YUAN 即
  BudgetExceeded——停批并告警（logger.error），**不静默降级**（设计 §12 护栏）。
- **审计**：每次调用（含失败）写 intel.llm_runs，成本可追溯到 doc_id + prompt_version。
- 成本折算：tokens × 价目（每百万 tokens 人民币，价目可配，需按 provider 实际价目校准）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from app.intel.core.config import settings
from app.intel.core.logging import get_logger
from app.intel.understand.prompts import PROMPT_VERSION

logger = get_logger(__name__)


class BudgetExceeded(RuntimeError):
    """当日 LLM 预算已用尽（停批告警，不静默降级）"""


class LLMAPIError(RuntimeError):
    """重试后仍失败的 API 错误"""


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cost_cny: float
    latency_ms: int
    model: str


class BudgetGate:
    """日预算闸：llm_runs 当日 sum(cost_cny) 对比 INTEL_DAILY_BUDGET_YUAN"""

    def __init__(self, store_mod=None):
        self._store = store_mod  # 便于测试注入

    def spent_today_cny(self) -> float:
        from app.intel import store
        s = self._store or store
        return s.llm_spent_today_cny()

    def check(self) -> None:
        budget = float(settings.INTEL_DAILY_BUDGET_YUAN)
        spent = self.spent_today_cny()
        if spent >= budget:
            logger.error(
                f"intel LLM 日预算已用尽：{spent:.2f}/{budget:.2f} 元，停批（不静默降级）",
                extra={"task": "intel_understand"},
            )
            raise BudgetExceeded(f"spent={spent:.2f} >= budget={budget:.2f}")


class LLMClient:
    """OpenAI 兼容客户端。extract_once 一次调用 = 预算闸 + 重试 + 计费 + 审计。"""

    def __init__(self, budget_gate: BudgetGate | None = None,
                 http_client: httpx.Client | None = None):
        self.gate = budget_gate or BudgetGate()
        self._http = http_client  # 测试可注入 mock transport

    @property
    def llm_version(self) -> str:
        return f"{settings.INTEL_LLM_PROVIDER}:{settings.INTEL_LLM_MODEL}"

    def _price(self) -> tuple[float, float]:
        """(输入, 输出) 每百万 tokens 人民币"""
        return (float(getattr(settings, "INTEL_LLM_PRICE_IN_CNY_PER_M", 2.0)),
                float(getattr(settings, "INTEL_LLM_PRICE_OUT_CNY_PER_M", 8.0)))

    def _audit(self, doc_id, model, in_toks, out_toks, cost, latency, status, error,
               prompt_version: str | None = None) -> None:
        from app.intel import store
        try:
            store.insert_llm_run({
                "doc_id": doc_id, "provider": settings.INTEL_LLM_PROVIDER, "model": model,
                "prompt_version": prompt_version or PROMPT_VERSION, "input_tokens": in_toks,
                "output_tokens": out_toks, "cost_cny": cost, "latency_ms": latency,
                "status": status, "error": error,
            })
        except Exception as e:  # noqa: BLE001 — 审计失败不炸主流程，但必须吼出来
            logger.error(f"llm_runs 审计写入失败: {e}", extra={"task": "intel_understand"})

    def extract_once(self, system: str, user: str, doc_id: int | None = None,
                     prompt_version: str | None = None) -> LLMResponse:
        """单次调用：预算闸 → HTTP（带重试）→ 计费 → 审计。失败路径先审计再抛。

        prompt_version: 默认抽取 prompt 版本（PROMPT_VERSION）；P4-4 风格总结等
        非抽取任务应传自己的版本（如 style_v1），避免成本归因混在抽取里。
        """
        self.gate.check()
        base_url = settings.INTEL_LLM_BASE_URL.rstrip("/")
        api_key = settings.INTEL_LLM_API_KEY
        model = settings.INTEL_LLM_MODEL
        if not (base_url and api_key and model):
            raise LLMAPIError("INTEL_LLM_BASE_URL / API_KEY / MODEL 未配置")

        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.1,   # 抽取任务要确定性
            # reasoning 模型（deepseek-v4-pro 等）reasoning tokens 计入 completion，
            # 上限太小会把 budget 全烧在思考上、content 为空；4096 够抽取+思考
            "max_tokens": int(getattr(settings, "INTEL_LLM_MAX_TOKENS", 4096)),
            "stream": False,
        }
        # JSON mode 开关：默认关。实测 vLLM + Qwen3.6 上 json_object guided 解码
        # 与部分输入病态交互（147s 生成 + 空 content）；系统提示词已强制 JSON
        # 且解析端有围栏/花括号兜底，关掉无合规风险。云端 DeepSeek 等可开。
        if bool(getattr(settings, "INTEL_LLM_JSON_MODE", False)):
            payload["response_format"] = {"type": "json_object"}
        extra_json = getattr(settings, "INTEL_LLM_EXTRA_BODY_JSON", "") or ""
        if extra_json.strip():
            try:
                payload.update(json.loads(extra_json))
            except json.JSONDecodeError as e:
                raise LLMAPIError(f"INTEL_LLM_EXTRA_BODY_JSON 非法: {e}") from e
        timeout_sec = float(getattr(settings, "INTEL_LLM_TIMEOUT_SEC", 120))
        price_in, price_out = self._price()
        last_err: str = ""

        for attempt in range(3):
            t0 = time.monotonic()
            try:
                http = self._http or httpx.Client(timeout=timeout_sec)
                try:
                    resp = http.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                finally:
                    if self._http is None:
                        http.close()
            except Exception as e:  # noqa: BLE001 — 网络/传输异常统一重试
                latency = int((time.monotonic() - t0) * 1000)
                last_err = f"{type(e).__name__}: {str(e)[:200]}"
                self._audit(doc_id, model, 0, 0, 0.0, latency, "api_fail", last_err,
                            prompt_version)
                time.sleep(1.5 * (attempt + 1))
                continue

            latency = int((time.monotonic() - t0) * 1000)
            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                self._audit(doc_id, model, 0, 0, 0.0, latency, "api_fail", last_err,
                            prompt_version)
                # 4xx（除 429）重试无意义，直接失败
                if resp.status_code < 500 and resp.status_code != 429:
                    break
                if resp.status_code == 429 and ("tpm" in resp.text.lower() or "429001" in resp.text):
                    # key 的每分钟 token 配额耗尽：短退避无意义，等一个配额窗口
                    time.sleep(float(getattr(settings, "INTEL_LLM_TPM_BACKOFF_SEC", 60)))
                else:
                    # 429（provider busy/限流）退避加倍：2/4/8s
                    time.sleep(2.0 * (2 ** attempt))
                continue

            try:
                data = resp.json()
                content = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage") or {}
                in_toks = int(usage.get("prompt_tokens") or 0)
                out_toks = int(usage.get("completion_tokens") or 0)
            except Exception as e:  # noqa: BLE001 — 响应结构异常，按失败重试
                last_err = f"bad response: {type(e).__name__}: {str(e)[:200]}"
                self._audit(doc_id, model, 0, 0, 0.0, latency, "api_fail", last_err,
                            prompt_version)
                time.sleep(2.0 * (2 ** attempt))
                continue

            if not content.strip():
                # vLLM 偶发 200+空 content（思考烧尽 budget 或 guided 解码 glitch），按可重试失败
                last_err = "empty content (reasoning budget or decoding glitch)"
                self._audit(doc_id, model, in_toks, out_toks, 0.0, latency, "api_fail", last_err)
                time.sleep(2.0 * (2 ** attempt))
                continue

            cost = in_toks / 1e6 * price_in + out_toks / 1e6 * price_out
            self._audit(doc_id, model, in_toks, out_toks, cost, latency, "ok", None,
                        prompt_version)
            return LLMResponse(content, in_toks, out_toks, cost, latency, model)

        raise LLMAPIError(f"重试 3 次仍失败: {last_err}")
