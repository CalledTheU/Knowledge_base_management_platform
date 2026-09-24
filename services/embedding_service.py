# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-24

import hashlib
import math
import re
import os
import threading


DIMENSION = 128
_model = None
_model_lock = threading.Lock()
_encode_lock = threading.Lock()


def _tokens(text):
    for token in re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+", text.lower()):
        yield token
        if len(token) > 1 and not re.fullmatch(r"[\u4e00-\u9fff]", token):
            yield from (token[i:i + 2] for i in range(len(token) - 1))


def embed(text):
    vector = [0.0] * DIMENSION
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSION
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def cosine(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def sparse_dot(left, right):
    if not left or not right:
        return 0.0
    return sum(float(value) * float(right.get(str(key), right.get(key, 0))) for key, value in left.items())


def _bge():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from pymilvus.model.hybrid import BGEM3EmbeddingFunction
                path = os.getenv("BGE_M3_PATH", r"D:\ai_models\modelscope_cache\models\BAAI\bge-m3")
                if not os.path.isdir(path):
                    raise RuntimeError(f"BGE-M3模型目录不存在: {path}")
                _model = BGEM3EmbeddingFunction(model_name=path, device=os.getenv("BGE_DEVICE", "cpu"), use_fp16=False)
    return _model


def encode_documents(texts):
    if os.getenv("KB_EMBEDDING_BACKEND", "bge").lower() == "hash":
        return [{"dense": embed(text), "sparse": None} for text in texts]
    with _encode_lock:
        result = _bge().encode_documents(texts)
    sparse = result.get("sparse")
    vectors = []
    for index, vector in enumerate(result["dense"]):
        sparse_vector = None
        if sparse is not None:
            start, end = sparse.indptr[index], sparse.indptr[index + 1]
            sparse_vector = dict(zip(sparse.indices[start:end].tolist(), sparse.data[start:end].tolist()))
        vectors.append({"dense": vector.tolist(), "sparse": sparse_vector})
    return vectors


def encode_query(text):
    if os.getenv("KB_EMBEDDING_BACKEND", "bge").lower() == "hash":
        return {"dense": embed(text), "sparse": None}
    with _encode_lock:
        result = _bge().encode_queries([text])
    sparse = result.get("sparse")
    sparse_vector = None
    if sparse is not None:
        sparse_vector = dict(zip(sparse.indices[sparse.indptr[0]:sparse.indptr[1]].tolist(),
                                 sparse.data[sparse.indptr[0]:sparse.indptr[1]].tolist()))
    return {"dense": result["dense"][0].tolist(), "sparse": sparse_vector}
