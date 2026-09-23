# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-23

import time
import uuid
from pathlib import Path

from fastapi import Depends, File, HTTPException, UploadFile

from schema.document_schema import ACLUpdate, DocumentUpdate


def register_router(app, *, db, require_permission, extract_file, split_text, uploads):
    require_knowledge = require_permission("knowledge.manage")
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

    @app.post("/api/documents/import")
    async def import_documents(files: list[UploadFile] = File(...), user=Depends(require_knowledge)):
        if len(files) > 30:
            raise HTTPException(400, "单次最多导入30个文件")
        results = []
        for file in files:
            filename = Path(file.filename or "").name
            if not filename or Path(filename).suffix.lower() not in {".pdf", ".md", ".txt", ".docx"}:
                raise HTTPException(415, f"不支持的文件格式: {filename}")
            raw = await file.read()
            if len(raw) > 20 * 1024 * 1024:
                raise HTTPException(413, f"文件超过20MB: {filename}")
            text = extract_file(filename, raw).strip()
            if not text:
                raise HTTPException(400, f"文件未提取到文本: {filename}")
            doc_id = str(uuid.uuid4())
            title = Path(filename).stem
            chunks = split_text(text)
            with db() as con:
                con.execute("INSERT INTO documents VALUES(?,?,?,?,?,1,?)", (doc_id, title, filename, "未分类", text, time.time()))
                con.execute("INSERT INTO acl VALUES(?,?,?)", (doc_id, "user", user["id"]))
                con.executemany("INSERT INTO chunks VALUES(?,?,?,?)", [(str(uuid.uuid4()), doc_id, i, chunk)
                                for i, chunk in enumerate(chunks)])
            (uploads / f"{doc_id}{Path(filename).suffix.lower()}").write_bytes(raw)
            results.append({"id": doc_id, "title": title, "chunks": len(chunks)})
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
            con.execute("DELETE FROM acl WHERE document_id=?", (document_id,))
            con.executemany("INSERT INTO acl VALUES(?,?,?)", [(document_id, p["kind"], p["subject"]) for p in body.permissions])
        return {"ok": True}

    @app.delete("/api/documents/{document_id}")
    def delete_document(document_id: str, user=Depends(require_knowledge)):
        with db() as con:
            con.execute("DELETE FROM documents WHERE id=?", (document_id,))
        return {"ok": True}
