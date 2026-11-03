"""向量：bge-m3。

硅基流动免费、0.25 秒、1024 维。检索时和关键词一起用——
关键词管"字面对得上"，向量管"意思相近但字不同"。
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence

DIM = 1024


def _service(cfg) -> dict:
    return (cfg.services.get("services") or {}).get("embed") or {}


def _endpoint(cfg) -> tuple[str, str, str]:
    name = _service(cfg).get("provider") or "siliconflow"
    provider = (cfg.providers.get("providers") or {}).get(name) or {}
    base = str(provider.get("base_url") or "").rstrip("/")
    ref = str(provider.get("key_ref") or "")

    key = os.environ.get(ref, "").strip()
    if not key and Path(os.path.expanduser("~/.config/siliconflow/key")).exists():
        key = Path(os.path.expanduser("~/.config/siliconflow/key")).read_text(encoding="utf-8").strip()
    return base, key, str(_service(cfg).get("model") or "BAAI/bge-m3")


def embed_texts(cfg, texts: Sequence[str], timeout: float = 30.0) -> list[list[float]]:
    """一批文本 → 一批向量。失败抛异常，调用方决定要不要退回关键词。"""
    items = [str(t or "")[:2000] for t in texts]
    if not items:
        return []

    base, key, model = _endpoint(cfg)
    if not base or not key:
        raise RuntimeError("embedding 服务没配好（providers.yml / key）")

    body = json.dumps({"model": model, "input": items}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/embeddings",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "xi-project/0.2",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [list(map(float, row["embedding"])) for row in data.get("data") or []]


def embed_one(cfg, text: str) -> list[float] | None:
    try:
        out = embed_texts(cfg, [text])
        return out[0] if out else None
    except Exception:  # noqa: BLE001 - 向量算不出来就只用关键词
        return None


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def pack(vector: Sequence[float]) -> bytes:
    """存库用的紧凑格式（1024 个 float32 = 4KB）。"""
    import array

    return array.array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    import array

    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)
