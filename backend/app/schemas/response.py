from typing import Generic, TypeVar, Optional, Any, List
from pydantic import BaseModel

T = TypeVar("T")

class Response(BaseModel, Generic[T]):
    code: int
    msg: str
    data: Optional[T] = None

    @classmethod
    def success(cls, data: T = None, msg: str = "success"):
        return cls(code=200, msg=msg, data=data)

    @classmethod
    def fail(cls, code: int = 400, msg: str = "fail", data: Any = None):
        return cls(code=code, msg=msg, data=data)

class PageData(BaseModel, Generic[T]):
    records: List[T]
    current: int
    size: int
    total: int

class PaginatedResponse(Response[PageData[T]], Generic[T]):
    pass
