from pydantic import BaseModel, Field


class UserInfoResponse(BaseModel):
    """与前端 @vben/types UserInfo 对齐"""

    avatar: str = ""
    realName: str = ""
    roles: list[str] = Field(default_factory=list)
    userId: str = ""
    username: str = ""
    desc: str = ""
    homePath: str = "/analytics"
    token: str = ""
