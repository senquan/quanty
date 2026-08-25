"""服务级异常定义"""


class DataCleanerError(Exception):
    """服务基础异常"""


class IngestionError(DataCleanerError):
    """数据接入失败（数据源不可用/限流/格式错误）"""


class PipelineValidationError(DataCleanerError):
    """清洗流水线输出校验失败"""


class FactorNotFoundError(DataCleanerError):
    """因子代码未注册"""


class FactorComputeError(DataCleanerError):
    """因子计算失败"""
