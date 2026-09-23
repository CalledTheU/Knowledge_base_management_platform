# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-23

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.documents_router import register_router as register_documents_router
from api.organization_router import register_router as register_organization_router
from schema.document_schema import ACLUpdate

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
DB_PATH = Path(os.getenv("KB_DB_PATH", DATA / "knowledge.db"))
UPLOADS.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="知识库管理平台", version="0.1.0")
PERMISSIONS = {"dashboard.view", "knowledge.manage", "curation.manage", "organization.manage", "chat.access"}
BUILTIN_PERMISSIONS = {
    "user": ["chat.access"],
    "knowledge_admin": ["dashboard.view", "knowledge.manage", "curation.manage", "chat.access"],
    "system_admin": sorted(PERMISSIONS),
    "management": ["dashboard.view", "chat.access"],
}


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240000).hex()
    return salt, digest


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS departments(id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT);
        CREATE TABLE IF NOT EXISTS roles(id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, built_in INTEGER NOT NULL DEFAULT 0,
            permissions TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
            password_salt TEXT NOT NULL, password_hash TEXT NOT NULL, department_id TEXT, roles TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '', content TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS acl(document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, subject TEXT NOT NULL, PRIMARY KEY(document_id,kind,subject));
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, content TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
            question TEXT NOT NULL, answer TEXT NOT NULL, retrieved TEXT NOT NULL, allowed TEXT NOT NULL,
            denied TEXT NOT NULL, tokens INTEGER NOT NULL, latency_ms INTEGER NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS gaps(id TEXT PRIMARY KEY, question TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL,
            department_id TEXT, count INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open');
        CREATE TABLE IF NOT EXISTS faqs(id TEXT PRIMARY KEY, question TEXT NOT NULL UNIQUE, answer TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate', count INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS faq_cache(question TEXT PRIMARY KEY, faq_id TEXT NOT NULL REFERENCES faqs(id));
        """)
        has_permissions = "permissions" in {r["name"] for r in con.execute("PRAGMA table_info(roles)")}
        if not has_permissions:
            con.execute("ALTER TABLE roles ADD COLUMN permissions TEXT NOT NULL DEFAULT '[]'")
        for role_id, label in [("user", "普通用户"), ("knowledge_admin", "知识管理员"),
                               ("system_admin", "系统管理员"), ("management", "管理层")]:
            permissions = json.dumps(BUILTIN_PERMISSIONS[role_id])
            con.execute("INSERT OR IGNORE INTO roles(id,name,built_in,permissions) VALUES(?,?,1,?)",
                        (role_id, label, permissions))
        built_in_roles = con.execute("SELECT id,permissions FROM roles WHERE id IN ('user','knowledge_admin','system_admin','management')").fetchall()
        if built_in_roles and all(not json.loads(role["permissions"]) for role in built_in_roles):
            for role_id, permissions in BUILTIN_PERMISSIONS.items():
                con.execute("UPDATE roles SET permissions=? WHERE id=?",
                            (json.dumps(permissions), role_id))
        if not con.execute("SELECT 1 FROM departments LIMIT 1").fetchone():
            con.executemany("INSERT INTO departments VALUES(?,?,?)", [
                ("dept-finance", "财务部", None), ("dept-hr", "人力资源部", None),
                ("dept-sales", "业务部", None)])
        if not con.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            for username, name, dept, roles, password in [
                ("admin", "系统管理员", "dept-hr", ["system_admin", "knowledge_admin"], "Admin123!"),
                ("finance", "财务用户", "dept-finance", ["user"], "Finance123!"),
                ("staff", "普通用户", "dept-sales", ["user"], "Staff123!")]:
                salt, digest = password_hash(password)
                con.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,1)",
                            (str(uuid.uuid4()), username, name, salt, digest, dept, json.dumps(roles)))
        if not con.execute("SELECT 1 FROM documents LIMIT 1").fetchone():
            samples = [
                ("差旅报销标准", "差旅报销标准.md", "财务制度", "员工出差前应提交出差申请。交通费用按实际合规票据报销，住宿标准按职级执行。出差结束后十个工作日内提交报销单。", [("global", "*")]),
                ("高管薪酬与股权激励细则", "高管薪酬制度.md", "人事制度", "高管薪酬资料仅限人力资源部和管理层角色查阅。股权激励归属安排由薪酬委员会审核。", [("department", "dept-hr"), ("role", "management")]),
                ("销售退换货处理规范", "退换货.md", "客服规范", "商品破损时请在签收后48小时内提交订单号、外包装和商品照片，客服核实后办理退款或补发。", [("role", "user")]),
            ]
            for title, filename, category, content, permissions in samples:
                doc_id = str(uuid.uuid4())
                con.execute("INSERT INTO documents VALUES(?,?,?,?,?,1,?)", (doc_id, title, filename, category, content, time.time()))
                con.executemany("INSERT INTO acl VALUES(?,?,?)", [(doc_id, *p) for p in permissions])
                for i, chunk in enumerate(split_text(content)):
                    con.execute("INSERT INTO chunks VALUES(?,?,?,?)", (str(uuid.uuid4()), doc_id, i, chunk))


def split_text(text, size=700):
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = []
    for part in parts:
        while len(part) > size:
            cut = part.rfind("。", 0, size)
            cut = cut + 1 if cut > size // 2 else size
            chunks.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            chunks.append(part)
    return chunks or ([text.strip()] if text.strip() else [])


def row_user(row):
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
            "department_id": row["department_id"], "roles": json.loads(row["roles"]), "active": bool(row["active"])}


def current_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "请先登录")
    token = authorization[7:]
    with db() as con:
        row = con.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1",
                          (token, time.time())).fetchone()
        permissions = set()
        if row:
            for role in con.execute("SELECT permissions FROM roles WHERE id IN (SELECT value FROM json_each(?))",
                                    (row["roles"],)):
                permissions.update(json.loads(role["permissions"]))
    if not row:
        raise HTTPException(401, "登录已失效")
    result = row_user(row)
    result["permissions"] = sorted(permissions)
    return result


def require_admin(user=Depends(current_user)):
    if "organization.manage" not in user["permissions"]:
        raise HTTPException(403, "缺少组织管理权限")
    return user


def require_permission(permission):
    def check(user=Depends(current_user)):
        if permission not in PERMISSIONS or permission not in user["permissions"]:
            raise HTTPException(403, "缺少功能权限")
        return user
    return check


def require_any_permission(*permissions):
    def check(user=Depends(current_user)):
        if not set(permissions).intersection(user["permissions"]):
            raise HTTPException(403, "缺少功能权限")
        return user
    return check


def allowed_for(user, acl):
    # Empty ACL means private; any matching ACL entity grants access.
    return any((kind == "global" and subject == "*") or
               (kind == "department" and subject == user["department_id"]) or
               (kind == "user" and subject == user["id"]) or
               (kind == "role" and subject in user["roles"]) for kind, subject in acl)


class Login(BaseModel):
    username: str
    password: str


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class FAQUpdate(BaseModel):
    question: str
    answer: str = ""
    status: str = "candidate"


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/login")
def login(body: Login):
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE username=? AND active=1", (body.username,)).fetchone()
        if not row:
            raise HTTPException(401, "用户名或密码错误")
        _, digest = password_hash(body.password, row["password_salt"])
        if not hmac.compare_digest(digest, row["password_hash"]):
            raise HTTPException(401, "用户名或密码错误")
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions VALUES(?,?,?)", (token, row["id"], time.time() + 86400))
    return {"token": token, "user": row_user(row)}


@app.post("/api/logout")
def logout(user=Depends(current_user), authorization: str = Header()):
    with db() as con:
        con.execute("DELETE FROM sessions WHERE token=?", (authorization[7:],))
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


def extract_file(filename, raw):
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return raw.decode("utf-8-sig", errors="replace")
    if suffix == ".pdf":
        from pypdf import PdfReader
        import io
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
    if suffix == ".docx":
        from docx import Document
        import io
        return "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
    raise HTTPException(415, "仅支持 PDF、Markdown、Word、TXT")


def words(text):
    result = set()
    for token in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            result.update(token[i:i + 2] for i in range(len(token) - 1))
            if len(token) == 1:
                result.add(token)
        elif len(token) > 1:
            result.add(token)
    return result


@app.post("/api/chat")
def ask(body: Ask, user=Depends(require_permission("chat.access"))):
    started = time.perf_counter()
    question = body.question.strip()
    with db() as con:
        cached = con.execute("SELECT f.* FROM faq_cache c JOIN faqs f ON f.id=c.faq_id WHERE c.question=?", (question,)).fetchone()
        rows = con.execute("SELECT c.id chunk_id,c.content,d.id document_id,d.title,d.filename,a.kind,a.subject FROM chunks c JOIN documents d ON d.id=c.document_id LEFT JOIN acl a ON a.document_id=d.id WHERE d.enabled=1 ORDER BY d.created DESC").fetchall()
    by_doc = {}
    for row in rows:
        item = by_doc.setdefault(row["document_id"], {"title": row["title"], "filename": row["filename"], "acl": [], "chunks": []})
        if row["kind"]:
            item["acl"].append((row["kind"], row["subject"]))
        item["chunks"].append({"id": row["chunk_id"], "content": row["content"]})
    qwords = words(question)
    ranked = []
    denied = []
    for doc_id, doc in by_doc.items():
        can_read = allowed_for(user, doc["acl"])
        best = max(doc["chunks"], key=lambda c: len(qwords & words(c["content"])), default=None)
        score = len(qwords & words(best["content"])) if best else 0
        if score:
            (ranked if can_read else denied).append((score, doc_id, doc, best))
    ranked.sort(reverse=True, key=lambda x: x[0])
    denied.sort(reverse=True, key=lambda x: x[0])
    citations = [{"document_id": doc_id, "title": doc["title"], "filename": doc["filename"], "chunk_id": chunk["id"], "excerpt": chunk["content"][:260]}
                 for _, doc_id, doc, chunk in ranked[:3]]
    if cached:
        answer = cached["answer"]
    elif citations:
        answer = "\n\n".join(f"{i}. {c['excerpt']}" for i, c in enumerate(citations, 1))
    else:
        answer = "目前没有检索到可用于回答的知识内容。问题已记录到知识缺口，管理员可据此补充资料。"
    if denied:
        answer += "\n\n部分参考资料因权限受限无法展示。"
    elapsed = int((time.perf_counter() - started) * 1000)
    chat_id = str(uuid.uuid4())
    with db() as con:
        con.execute("INSERT INTO chats VALUES(?,?,?,?,?,?,?,?,?,?,?)", (chat_id, body.session_id or str(uuid.uuid4()),
            user["id"], question, answer, json.dumps([x[1] for x in ranked + denied]),
            json.dumps([x[1] for x in ranked]), json.dumps([x[1] for x in denied]),
            max(1, (len(question) + len(answer)) // 4), elapsed, time.time()))
        if not citations:
            con.execute("INSERT INTO gaps(id,question,user_id,department_id,created) VALUES(?,?,?,?,?) ON CONFLICT(question) DO UPDATE SET count=count+1",
                        (str(uuid.uuid4()), question, user["id"], user["department_id"], time.time()))
        if not cached:
            con.execute("INSERT INTO faqs(id,question,answer,status,count,created) VALUES(?,?,?,?,1,?) ON CONFLICT(question) DO UPDATE SET count=count+1",
                        (str(uuid.uuid4()), question, answer, "candidate", time.time()))
    return {"id": chat_id, "question": question, "answer": answer, "citations": citations,
            "restricted": bool(denied), "faq_hit": bool(cached), "latency_ms": elapsed}


@app.get("/api/dashboard")
def dashboard(user=Depends(require_permission("dashboard.view"))):
    with db() as con:
        return {
            "questions": con.execute("SELECT count(*) FROM chats").fetchone()[0],
            "uv": con.execute("SELECT count(DISTINCT user_id) FROM chats").fetchone()[0],
            "knowledge": con.execute("SELECT count(*) FROM documents").fetchone()[0],
            "tokens": con.execute("SELECT coalesce(sum(tokens),0) FROM chats").fetchone()[0],
            "avg_latency": con.execute("SELECT coalesce(avg(latency_ms),0) FROM chats").fetchone()[0],
            "faq_hits": con.execute("SELECT count(*) FROM chats WHERE question IN (SELECT question FROM faq_cache)").fetchone()[0],
            "top_questions": [dict(r) for r in con.execute("SELECT question,count(*) count FROM chats GROUP BY question ORDER BY count DESC LIMIT 8")],
            "top_knowledge": [dict(r) for r in con.execute("SELECT d.title,count(*) count FROM chats c,json_each(c.allowed) j JOIN documents d ON d.id=j.value GROUP BY d.id ORDER BY count DESC LIMIT 8")],
        }


@app.get("/api/faqs")
def faqs(user=Depends(require_permission("curation.manage"))):
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM faqs ORDER BY count DESC,created DESC")]


@app.put("/api/faqs/{faq_id}")
def update_faq(faq_id: str, body: FAQUpdate, user=Depends(require_permission("curation.manage"))):
    if body.status not in {"candidate", "published", "rejected"}:
        raise HTTPException(400, "FAQ状态无效")
    with db() as con:
        cur = con.execute("UPDATE faqs SET question=?,answer=?,status=? WHERE id=?", (body.question, body.answer, body.status, faq_id))
        if not cur.rowcount:
            raise HTTPException(404, "FAQ不存在")
        con.execute("DELETE FROM faq_cache WHERE faq_id=?", (faq_id,))
        if body.status == "published":
            con.execute("INSERT OR REPLACE INTO faq_cache VALUES(?,?)", (body.question, faq_id))
    return {"ok": True}


@app.get("/api/gaps")
def gaps(user=Depends(require_permission("curation.manage"))):
    with db() as con:
        return [dict(r) for r in con.execute("SELECT g.*,d.name department FROM gaps g LEFT JOIN departments d ON d.id=g.department_id ORDER BY count DESC,created DESC")]


@app.put("/api/gaps/{gap_id}")
def update_gap(gap_id: str, body: dict, user=Depends(require_permission("curation.manage"))):
    status = body.get("status", "open")
    if status not in {"open", "in_progress", "resolved"}:
        raise HTTPException(400, "状态无效")
    with db() as con:
        con.execute("UPDATE gaps SET status=? WHERE id=?", (status, gap_id))
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(ROOT / "front" / "index.html")


register_documents_router(app, db=db, require_permission=require_permission,
                         extract_file=extract_file, split_text=split_text, uploads=UPLOADS)
register_organization_router(app, db=db, current_user=current_user,
                             require_permission=require_permission, require_any_permission=require_any_permission,
                             row_user=row_user, password_hash=password_hash,
                             permissions=PERMISSIONS)

app.mount("/static", StaticFiles(directory=ROOT / "front"), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
