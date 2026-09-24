# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-23

import asyncio
import time
import uuid
import json
import logging
from pathlib import Path

from fastapi import Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from schema.document_schema import ACLUpdate, ChunkUpdate, DocumentUpdate
from services.embedding_service import encode_documents
from services import rag_service

logger = logging.getLogger("knowledge_base")
_import_events = {}


def register_router(app, *, db, require_permission, extract_file, validate_text, split_text, uploads):
    require_knowledge = require_permission("knowledge.manage")

    @app.get("/api/documents/import/events/{request_id}")
    async def import_events(request_id: str, user=Depends(require_knowledge)):
        queue = _import_events.setdefault(request_id, asyncio.Queue())

        async def stream():
            try:
                while True:
                    event = await queue.get()
                    yield f"event: import\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("done"):
                        break
            finally:
                _import_events.pop(request_id, None)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    @app.get("/api/documents")
    def documents(user=Depends(require_knowledge)):
        with db() as con:
            rows = con.execute("SELECT d.*,count(c.id) chunks FROM documents d LEFT JOIN chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created DESC").fetchall()
            result = []
            for row in rows:
                acl = con.execute("SELECT kind,subject FROM acl WHERE document_id=?", (row["id"],)).fetchall()
                permissions = [dict(x) for x in acl]
                result.append({**dict(row), "permissions": permissions,
                               "readable": any((kind == "global" and subject == "*") or
                                               (kind == "department" and subject == user["department_id"]) or
                                               (kind == "user" and subject == user["id"]) or
                                               (kind == "role" and subject in user["roles"])
                                               for kind, subject in ((x["kind"], x["subject"]) for x in acl))})
        return result

    @app.get("/api/acl-users")
    def acl_users(user=Depends(require_knowledge)):
        with db() as con:
            return [dict(r) for r in con.execute("SELECT id,display_name FROM users WHERE active=1 ORDER BY display_name")]

    @app.get("/api/documents/{document_id}")
    def document_detail(document_id: str, user=Depends(require_knowledge)):
        with db() as con:
            document = con.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if not document:
                raise HTTPException(404, "知识不存在")
            chunks = con.execute("SELECT id,ordinal,content FROM chunks WHERE document_id=? ORDER BY ordinal", (document_id,)).fetchall()
            return {**dict(document), "chunks": [dict(chunk) for chunk in chunks]}

    @app.put("/api/chunks/{chunk_id}")
    def update_chunk(chunk_id: str, body: ChunkUpdate, user=Depends(require_knowledge)):
        content = body.content.strip()
        if not content:
            raise HTTPException(400, "切片内容不能为空")
        with db() as con:
            chunk = con.execute("SELECT document_id,ordinal FROM chunks WHERE id=?", (chunk_id,)).fetchone()
            if not chunk:
                raise HTTPException(404, "切片不存在")
            vector = encode_documents([content])[0]
            indexed = int(rag_service.external_enabled())
            if indexed:
                rag_service.upsert_vectors([{
                    "chunk_id": chunk_id, "document_id": chunk["document_id"],
                    "dense_vector": vector["dense"], "sparse_vector": vector["sparse"] or {},
                }])
            con.execute("UPDATE chunks SET content=?,dense_vector=?,sparse_vector=?,vector_indexed=? WHERE id=?",
                        (content, json.dumps(vector["dense"]), json.dumps(vector["sparse"]), indexed, chunk_id))
            merged = "\n\n".join(row["content"] for row in con.execute(
                "SELECT content FROM chunks WHERE document_id=? ORDER BY ordinal", (chunk["document_id"],)))
            con.execute("UPDATE documents SET content=? WHERE id=?", (merged, chunk["document_id"]))
        return {"ok": True}

    @app.post("/api/documents/import")
    async def import_documents(files: list[UploadFile] = File(...),
                               x_import_request_id: str | None = Header(default=None),
                               user=Depends(require_knowledge)):
        if len(files) > 30:
            raise HTTPException(400, "单次最多导入30个文件")
        results = []
        for file in files:
            filename = Path(file.filename or "").name
            request_id = x_import_request_id or str(uuid.uuid4())
            raw = None
            doc_id = None
            chunk_ids = []
            vector_attempted = False
            source_object = None
            local_path = None
            stages = []

            def stage(message, level="info"):
                stages.append(message)
                getattr(logger, level, logger.info)(
                    "IMPORT_STAGE filename=%r stage=%s", filename, message,
                    extra={"request_id": request_id})
                queue = _import_events.get(request_id)
                if queue:
                    queue.put_nowait({"request_id": request_id, "stage": message, "done": False})

            try:
                if not filename or Path(filename).suffix.lower() not in {".pdf", ".md", ".txt", ".docx"}:
                    raise HTTPException(415, f"不支持的文件格式: {filename}")
                raw = await file.read()
                stage(f"读取文件完成，大小 {len(raw)} 字节")
                if len(raw) > 20 * 1024 * 1024:
                    raise HTTPException(413, f"文件超过20MB: {filename}")
                stage("正在解析文件内容")
                text = validate_text(extract_file(filename, raw), filename)
                stage(f"文件解析完成，文本长度 {len(text)} 字符")
                doc_id = str(uuid.uuid4())
                title = Path(filename).stem
                stage("正在切分档案内容")
                chunks = split_text(text)
                stage(f"档案切分完成，共 {len(chunks)} 个知识切片")
                stage("正在进行 BGE-M3 向量化")
                vectors = encode_documents(chunks)
                if len(vectors) != len(chunks) or any(not vector["dense"] for vector in vectors):
                    raise RuntimeError("Embedding returned incomplete chunk vectors")
                stage(f"向量化完成，生成 {len(vectors)} 组稠密/稀疏向量")
                chunk_ids = [str(uuid.uuid4()) for _ in chunks]
                indexed = int(rag_service.external_enabled())
                if indexed:
                    stage("正在上传原文件到 MinIO")
                    source_object = rag_service.store_source(doc_id, filename, raw)
                    stage("MinIO 原文件上传完成")
                    vector_attempted = True
                    stage("正在写入 Milvus 向量索引")
                    rag_service.upsert_vectors([{
                        "chunk_id": chunk_ids[i], "document_id": doc_id,
                        "dense_vector": vectors[i]["dense"], "sparse_vector": vectors[i]["sparse"] or {},
                    } for i in range(len(chunks))])
                    stage("Milvus 向量索引写入完成")
                local_path = uploads / f"{doc_id}{Path(filename).suffix.lower()}"
                local_path.write_bytes(raw)
                stage("正在保存 SQLite 文档、权限和切片元数据")
                with db() as con:
                    con.execute(
                        "INSERT INTO documents(id,title,filename,category,content,enabled,created,source_object) VALUES(?,?,?,?,?,1,?,?)",
                        (doc_id, title, filename, "未分类", text, time.time(), source_object))
                    con.execute("INSERT INTO acl VALUES(?,?,?)", (doc_id, "user", user["id"]))
                    con.executemany(
                        "INSERT INTO chunks(id,document_id,ordinal,content,dense_vector,sparse_vector,vector_indexed) VALUES(?,?,?,?,?,?,?)",
                        [(chunk_ids[i], doc_id, i, chunk, json.dumps(vectors[i]["dense"]),
                          json.dumps(vectors[i]["sparse"]), indexed) for i, chunk in enumerate(chunks)])
                stage("导入完成")
                queue = _import_events.get(request_id)
                if queue:
                    queue.put_nowait({"request_id": request_id, "stage": "导入完成", "done": True})
                results.append({"id": doc_id, "title": title, "chunks": len(chunks), "stages": stages})
            except HTTPException:
                if source_object:
                    try:
                        rag_service.remove_source(source_object)
                    except Exception:
                        logger.exception("Import rollback could not remove MinIO object",
                                         extra={"request_id": request_id})
                if vector_attempted:
                    try:
                        rag_service.delete_vectors(chunk_ids)
                    except Exception:
                        logger.exception("Import rollback could not remove Milvus vectors",
                                         extra={"request_id": request_id})
                if local_path:
                    local_path.unlink(missing_ok=True)
                queue = _import_events.get(request_id)
                if queue:
                    queue.put_nowait({"request_id": request_id, "stage": "导入失败，请检查文件格式或权限", "done": True,
                                      "error": True})
                raise
            except Exception:
                if source_object:
                    try:
                        rag_service.remove_source(source_object)
                    except Exception:
                        logger.exception("Import rollback could not remove MinIO object",
                                         extra={"request_id": request_id})
                if vector_attempted:
                    try:
                        rag_service.delete_vectors(chunk_ids)
                    except Exception:
                        logger.exception("Import rollback could not remove Milvus vectors",
                                         extra={"request_id": request_id})
                if local_path:
                    local_path.unlink(missing_ok=True)
                logger.exception("Document import failed filename=%r size=%s", filename,
                                 len(raw) if raw is not None else "unknown",
                                 extra={"request_id": request_id})
                queue = _import_events.get(request_id)
                if queue:
                    queue.put_nowait({"request_id": request_id, "stage": "导入失败，请查看错误日志", "done": True,
                                      "error": True})
                raise HTTPException(500, f"导入失败，错误编号：{request_id}") from None
        return {"imported": results}

    @app.put("/api/documents/{document_id}")
    def update_document(document_id: str, body: DocumentUpdate, user=Depends(require_knowledge)):
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "标题不能为空")
        with db() as con:
            cur = con.execute("UPDATE documents SET title=?,category=?,enabled=? WHERE id=?",
                              (title, body.category.strip(), body.enabled, document_id))
            if not cur.rowcount:
                raise HTTPException(404, "知识不存在")
        return {"ok": True}

    @app.put("/api/documents/{document_id}/acl")
    def set_acl(document_id: str, body: ACLUpdate, user=Depends(require_knowledge)):
        valid_kinds = {"global", "department", "role", "user"}
        if any(p.get("kind") not in valid_kinds or not p.get("subject") or
               (p["kind"] == "global" and p["subject"] != "*") for p in body.permissions):
            raise HTTPException(400, "权限实体无效")
        with db() as con:
            if not con.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone():
                raise HTTPException(404, "知识不存在")
            invalid = []
            for permission in body.permissions:
                kind, subject = permission["kind"], permission["subject"]
                table = {"department": "departments", "role": "roles", "user": "users"}.get(kind)
                if table and not con.execute(
                        f"SELECT 1 FROM {table} WHERE id=?" + (" AND active=1" if kind == "user" else ""),
                        (subject,)).fetchone():
                    invalid.append(f"{kind}:{subject}")
            if invalid:
                raise HTTPException(400, "invalid ACL subjects: " + ", ".join(invalid))
            con.execute("DELETE FROM acl WHERE document_id=?", (document_id,))
            con.executemany("INSERT INTO acl VALUES(?,?,?)", [(document_id, p["kind"], p["subject"]) for p in body.permissions])
        return {"ok": True}

    @app.delete("/api/documents/{document_id}")
    def delete_document(document_id: str, user=Depends(require_knowledge)):
        with db() as con:
            document = con.execute(
                "SELECT filename,source_object FROM documents WHERE id=?", (document_id,)).fetchone()
            if not document:
                raise HTTPException(404, "知识不存在")
            chunk_ids = [row["id"] for row in con.execute(
                "SELECT id FROM chunks WHERE document_id=?", (document_id,))]
        if rag_service.external_enabled():
            rag_service.delete_vectors(chunk_ids)
            rag_service.remove_source(document["source_object"])
        with db() as con:
            con.execute("DELETE FROM documents WHERE id=?", (document_id,))
        (uploads / f"{document_id}{Path(document['filename']).suffix.lower()}").unlink(missing_ok=True)
        return {"ok": True}
