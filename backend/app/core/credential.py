"""凭证/密钥的安全存储与读取（api_key 明文治理，设计文档 §8 / §11）。

治理目标
--------
- `cleaner_services.api_key` 原本**明文**落库（模型注释写 "AES 存储占位" 实为明文）。
- 现改为 **AES(Fernet) 加密存储**，并支持 **多 key 逗号分隔并存与轮换**。
- **迁移期兼容**：读取时若遇到非加密的存量明文，按明文使用并告警一次；
  启动期由 `cleaner_gateway.migrate_api_keys` 把这些明文自动重写为加密形态，
  最终实现「库内零明文」。

实现要点
--------
- 密钥派生自 `settings.SECRET_KEY`（`cryptography.fernet` 要求 32 字节 url-safe
  base64 密钥，用 SHA256(SECRET_KEY) 派生）。生产请为 `SECRET_KEY` 配置强随机值，
  且**不要提交到仓库**（`.env` 已被 gitignore）。
- 存储格式：`ENC:` + Fernet token；多 key 以逗号连接后整体加密。
- 解密失败（密钥变更等）按明文回退并告警，避免「一次失败全量鉴权挂掉」。

注：本模块原名设想放在 `security.py`，但该名已被 JWT 鉴权模块占用，故独立成
`credential.py`，避免覆盖既有认证逻辑。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import threading
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "ENC:"
_warn_once: set[str] = set()
_lock = threading.Lock()


def _fernet() -> Fernet:
    seed = (settings.SECRET_KEY or "dev-insecure-secret-key").encode("utf-8")
    # SHA256 派生 32 字节 → urlsafe base64，恰好满足 Fernet 密钥长度
    key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    """把单个字符串加密为 `ENC:...` 存储形态。"""
    return _PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(maybe: str | None) -> str | None:
    """解密；遇到密文解密失败或存量明文时回退明文并告警。"""
    if not maybe:
        return None
    if maybe.startswith(_PREFIX):
        try:
            return _fernet().decrypt(maybe[len(_PREFIX) :].encode("ascii")).decode("utf-8")
        except (InvalidToken, Exception) as e:  # noqa: BLE001
            logger.error("api_key 解密失败，按明文回退: %s", e)
            return maybe[len(_PREFIX) :]
    # 存量明文：告警一次后按明文使用（启动期会被自动重加密）
    with _lock:
        if "legacy_plaintext" not in _warn_once:
            _warn_once.add("legacy_plaintext")
            logger.warning(
                "检测到未加密明文 api_key，按明文使用；启动期将自动重加密，建议尽快轮换"
            )
    return maybe


def is_encrypted(stored: str | None) -> bool:
    return bool(stored) and stored.startswith(_PREFIX)


def store_keys(keys: Iterable[str]) -> str:
    """把多个 key 归一为可存储字符串（逗号连接后整体加密）。无 key 返回空串。"""
    cleaned = [k.strip() for k in keys if k and str(k).strip()]
    if not cleaned:
        return ""
    return encrypt_secret(",".join(cleaned))


def load_keys(stored: str | None) -> list[str]:
    """从存储字符串还原 key 列表。

    - 密文：解密后按逗号拆分。
    - 存量明文：整体作为单个 key 返回（兼容迁移期）。
    """
    plain = decrypt_secret(stored)
    if not plain:
        return []
    return [k.strip() for k in plain.split(",") if k.strip()]
