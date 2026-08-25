"""清洗流水线编排器

按顺序执行 6 个 Transformer，并汇总每步报告。
"""
import time

import pandas as pd

from app.core.exceptions import PipelineValidationError
from app.core.logging import get_logger
from app.pipeline.adjust import AdjustTransformer
from app.pipeline.align import TimeAlignTransformer
from app.pipeline.base import Transformer
from app.pipeline.deduplicate import DeduplicateTransformer
from app.pipeline.missing_value import MissingValueTransformer
from app.pipeline.outlier import OutlierTransformer
from app.pipeline.validate import ValidateTransformer

logger = get_logger(__name__)

_DEFAULT_STEPS: list[Transformer] = [
    DeduplicateTransformer(),
    MissingValueTransformer(),
    OutlierTransformer(),
    AdjustTransformer(),
    TimeAlignTransformer(),
    ValidateTransformer(),
]


class CleaningPipeline:
    def __init__(self, steps: list[Transformer] | None = None):
        self.steps = steps or _DEFAULT_STEPS

    def run(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        """执行清洗，返回 (清洗后DataFrame, 运行报告)"""
        report_steps: list[dict] = []
        rows_in = int(len(df))
        start = time.perf_counter()

        current = df
        for step in self.steps:
            before = current
            try:
                current = step.transform(current)
            except PipelineValidationError:
                raise
            except Exception as e:  # 其它步骤异常包装为 PipelineValidationError
                raise PipelineValidationError(f"步骤 {step.name} 失败: {e}") from e
            report_steps.append(step._report(before, current))

        duration_ms = int((time.perf_counter() - start) * 1000)
        report = {
            "rows_in": rows_in,
            "rows_out": int(len(current)),
            "duration_ms": duration_ms,
            "steps": report_steps,
        }

        # pandera 结构校验（§10 工程规范）
        from app.pipeline.schema_check import validate_cleaned

        validate_cleaned(current)

        logger.info(
            "清洗流水线完成",
            extra={"task": "pipeline", "rows_in": rows_in,
                   "duration_ms": duration_ms},
        )
        return current, report
