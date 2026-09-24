# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-23

import os
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class PlatformFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["KB_DB_PATH"] = os.path.join(self.temp.name, "test.db")
        os.environ["KB_EMBEDDING_BACKEND"] = "hash"
        os.environ["KB_RAG_BACKEND"] = "sqlite"
        import main
        main.DB_PATH = os.environ["KB_DB_PATH"]
        self.app = main.app
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()
        os.environ.pop("KB_DB_PATH", None)
        os.environ.pop("KB_EMBEDDING_BACKEND", None)
        os.environ.pop("KB_RAG_BACKEND", None)

    def login(self, username, password):
        response = self.client.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def test_acl_filter_and_faq_cache(self):
        finance = self.login("finance", "Finance123!")
        staff = self.login("staff", "Staff123!")
        secret = "高管薪酬与股权激励细则"
        denied = self.client.post("/api/chat", headers=staff, json={"question": secret}).json()
        self.assertTrue(denied["restricted"])
        self.assertNotIn("薪酬委员会审核", denied["answer"])
        allowed = self.client.post("/api/chat", headers=finance, json={"question": "差旅报销标准"}).json()
        self.assertTrue(allowed["citations"])

        admin = self.login("admin", "Admin123!")
        faq_id = self.client.get("/api/faqs", headers=admin).json()[0]["id"]
        faq = self.client.get("/api/faqs", headers=admin).json()[0]
        self.client.put(f"/api/faqs/{faq_id}", headers=admin, json={"question": faq["question"],
                         "answer": "已审核标准答案", "status": "published"})
        cached = self.client.post("/api/chat", headers=admin, json={"question": faq["question"]}).json()
        self.assertTrue(cached["faq_hit"])
        self.assertEqual(cached["answer"], "已审核标准答案")

        similar = self.client.post("/api/chat", headers=admin, json={"question": faq["question"] + "？"}).json()
        self.assertTrue(similar["faq_hit"])
        self.assertEqual(similar["answer"], "已审核标准答案")
        self.assertEqual(self.client.put(f"/api/faqs/{faq_id}/cache", headers=admin,
                                         json={"enabled": False}).status_code, 200)
        disabled = self.client.post("/api/chat", headers=admin, json={"question": faq["question"]}).json()
        self.assertFalse(disabled["faq_hit"])

    def test_miss_creates_gap(self):
        finance = self.login("finance", "Finance123!")
        response = self.client.post("/api/chat", headers=finance, json={"question": "火星基地供水系统方案"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["citations"])
        admin = self.login("admin", "Admin123!")
        gaps = self.client.get("/api/gaps", headers=admin).json()
        gap = next(x for x in gaps if x["question"] == "火星基地供水系统方案")
        task = self.client.post(f"/api/gaps/{gap['id']}/task", headers=admin)
        self.assertEqual(task.status_code, 200)
        self.assertEqual(self.client.post(f"/api/gaps/{gap['id']}/task", headers=admin).json()["id"], task.json()["id"])
        self.assertEqual(next(x for x in self.client.get("/api/gaps", headers=admin).json()
                              if x["id"] == gap["id"])["task_id"], task.json()["id"])

    def test_similar_questions_cluster_into_one_candidate(self):
        staff = self.login("staff", "Staff123!")
        admin = self.login("admin", "Admin123!")
        first = "海外直邮清关延误怎么办"
        second = "海外直邮清关延误了怎么办"
        self.client.post("/api/chat", headers=staff, json={"question": first})
        self.client.post("/api/chat", headers=staff, json={"question": second})
        matches = [faq for faq in self.client.get("/api/faqs", headers=admin).json()
                   if faq["question"] in {first, second}]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["count"], 2)

    def test_document_edit_disable_and_permissions(self):
        admin = self.login("admin", "Admin123!")
        doc = next(d for d in self.client.get("/api/documents", headers=admin).json()
                   if d["title"] == "差旅报销标准")
        updated = self.client.put(f"/api/documents/{doc['id']}", headers=admin,
                                  json={"title": "差旅标准（更新）", "category": "制度", "enabled": False})
        self.assertEqual(updated.status_code, 200)
        saved = next(d for d in self.client.get("/api/documents", headers=admin).json()
                     if d["id"] == doc["id"])
        self.assertEqual((saved["title"], saved["category"], saved["enabled"]),
                         ("差旅标准（更新）", "制度", 0))
        answer = self.client.post("/api/chat", headers=self.login("finance", "Finance123!"),
                                  json={"question": "差旅报销标准"}).json()
        self.assertFalse(any(c["document_id"] == doc["id"] for c in answer["citations"]))
        denied = self.client.put(f"/api/documents/{doc['id']}", headers=self.login("staff", "Staff123!"),
                                 json={"title": "越权", "category": "制度", "enabled": True})
        self.assertEqual(denied.status_code, 403)

    def test_department_tree_and_role_lifecycle(self):
        admin = self.login("admin", "Admin123!")
        staff = self.login("staff", "Staff123!")
        root = self.client.post("/api/departments", headers=admin,
                                json={"name": "产品部"}).json()
        child = self.client.post("/api/departments", headers=admin,
                                 json={"name": "研发组", "parent_id": root["id"]}).json()
        self.assertEqual(self.client.put(f"/api/departments/{root['id']}", headers=admin,
                                         json={"name": "产品部", "parent_id": child["id"]}).status_code, 400)
        self.assertEqual(self.client.delete(f"/api/departments/{root['id']}", headers=admin).status_code, 409)
        self.assertEqual(self.client.post("/api/departments", headers=staff,
                                         json={"name": "越权部门"}).status_code, 403)

        role = self.client.post("/api/roles", headers=admin,
                                json={"name": "审计员", "permissions": ["chat.access"]}).json()
        self.assertEqual(self.client.put("/api/users/unknown", headers=staff, json={
            "display_name": "X", "department_id": None, "roles": [role["id"]]}).status_code, 403)
        user_id = self.client.post("/api/users", headers=admin, json={
            "username": "auditor", "display_name": "审计员甲", "password": "Auditor123!",
            "department_id": child["id"], "roles": [role["id"]]}).json()["id"]
        self.assertEqual(self.client.delete(f"/api/roles/{role['id']}", headers=admin).status_code, 409)
        secret = next(d for d in self.client.get("/api/documents", headers=admin).json()
                      if d["title"] == "高管薪酬与股权激励细则")
        self.client.put(f"/api/documents/{secret['id']}/acl", headers=admin,
                        json={"permissions": [{"kind": "role", "subject": role["id"]}]})
        auditor = self.login("auditor", "Auditor123!")
        response = self.client.post("/api/chat", headers=auditor,
                                    json={"question": "高管薪酬与股权激励细则"}).json()
        self.assertTrue(any(c["document_id"] == secret["id"] for c in response["citations"]))
        renamed = self.client.put(f"/api/roles/{role['id']}", headers=admin,
                                  json={"name": "内审员", "permissions": ["chat.access"]})
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/roles/{role['id']}", headers=admin).status_code, 409)
        self.client.put(f"/api/documents/{secret['id']}/acl", headers=admin,
                        json={"permissions": []})
        self.assertEqual(self.client.put(f"/api/users/{user_id}", headers=admin, json={
            "display_name": "审计员甲", "department_id": root["id"], "roles": ["user"]}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/roles/{role['id']}", headers=admin).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/departments/{child['id']}", headers=admin).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/departments/{root['id']}", headers=admin).status_code, 409)

    def test_user_status_revokes_sessions_and_protects_admin(self):
        admin = self.login("admin", "Admin123!")
        staff = self.login("staff", "Staff123!")
        staff_id = self.client.get("/api/users", headers=admin).json()
        staff_id = next(u["id"] for u in staff_id if u["username"] == "staff")

        self.assertEqual(self.client.patch(f"/api/users/{staff_id}/status", headers=staff,
                                           json={"active": False}).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/users/{staff_id}/status", headers=admin,
                                           json={"active": False}).status_code, 200)
        self.assertEqual(self.client.get("/api/me", headers=staff).status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={
            "username": "staff", "password": "Staff123!"}).status_code, 401)
        self.assertEqual(self.client.patch(f"/api/users/{staff_id}/status", headers=admin,
                                           json={"active": True}).status_code, 200)
        self.assertEqual(self.client.post("/api/login", json={
            "username": "staff", "password": "Staff123!"}).status_code, 200)

        admin_id = next(u["id"] for u in self.client.get("/api/users", headers=admin).json()
                        if u["username"] == "admin")
        self.assertEqual(self.client.patch(f"/api/users/{admin_id}/status", headers=admin,
                                           json={"active": False}).status_code, 409)

    def test_function_permissions_are_enforced_and_change_immediately(self):
        user = self.login("staff", "Staff123!")
        for path in ("/dashboard", "/documents", "/faqs", "/departments", "/roles", "/users"):
            self.assertEqual(self.client.get(f"/api{path}", headers=user).status_code, 403, path)
        self.assertEqual(self.client.post("/api/chat", headers=user,
                                          json={"question": "差旅报销"}).status_code, 200)

        admin = self.login("admin", "Admin123!")
        role_response = self.client.post("/api/roles", headers=admin,
                                         json={"name": "运营查看", "permissions": ["dashboard.view"]})
        self.assertEqual(role_response.status_code, 200)
        role_id = role_response.json()["id"]
        created = self.client.post("/api/users", headers=admin, json={
            "username": "viewer", "display_name": "运营查看者", "password": "Viewer123!",
            "roles": [role_id]})
        self.assertEqual(created.status_code, 200)
        viewer = self.login("viewer", "Viewer123!")
        self.assertEqual(self.client.get("/api/dashboard", headers=viewer).status_code, 200)
        self.assertEqual(self.client.post("/api/chat", headers=viewer,
                                          json={"question": "差旅报销"}).status_code, 403)
        self.assertEqual(self.client.get("/api/roles", headers=viewer).status_code, 403)

        updated = self.client.put(f"/api/roles/{role_id}", headers=admin,
                                  json={"name": "运营查看", "permissions": ["chat.access"]})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard", headers=viewer).status_code, 403)
        self.assertEqual(self.client.post("/api/chat", headers=viewer,
                                          json={"question": "差旅报销"}).status_code, 200)
        invalid = self.client.put(f"/api/roles/{role_id}", headers=admin,
                                  json={"name": "运营查看", "permissions": ["admin.root"]})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.get("/api/me", headers=viewer).json()["permissions"], ["chat.access"])

        knowledge_admin = self.client.post("/api/users", headers=admin, json={
            "username": "knowledge", "display_name": "知识管理员", "password": "Knowledge123!",
            "roles": ["knowledge_admin"]})
        self.assertEqual(knowledge_admin.status_code, 200)
        knowledge_headers = self.login("knowledge", "Knowledge123!")
        self.assertEqual(self.client.get("/api/documents", headers=knowledge_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/departments", headers=knowledge_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/roles", headers=knowledge_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/users", headers=knowledge_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/departments", headers=knowledge_headers,
                                          json={"name": "越权部门"}).status_code, 403)

    def test_role_permission_change_cannot_remove_last_organization_admin(self):
        admin = self.login("admin", "Admin123!")
        failed = self.client.put("/api/roles/system_admin", headers=admin, json={
            "name": "系统管理员", "permissions": ["chat.access"]})
        self.assertEqual(failed.status_code, 409)
        self.assertIn("organization.manage", self.client.get("/api/me", headers=admin).json()["permissions"])

    def test_acl_and_curation_validate_references(self):
        admin = self.login("admin", "Admin123!")
        doc = self.client.get("/api/documents", headers=admin).json()[0]
        self.assertEqual(self.client.put(f"/api/documents/{doc['id']}/acl", headers=admin, json={
            "permissions": [{"kind": "role", "subject": "missing-role"}]}).status_code, 400)
        self.client.post("/api/chat", headers=admin, json={"question": "validation faq seed"})
        faq = self.client.get("/api/faqs", headers=admin).json()[0]
        self.assertEqual(self.client.put(f"/api/faqs/{faq['id']}", headers=admin, json={
            "question": faq["question"], "answer": "", "status": "published"}).status_code, 400)
        self.assertEqual(self.client.put("/api/gaps/missing", headers=admin,
                                         json={"status": "resolved"}).status_code, 404)

    def test_import_embedding_failure_is_logged_with_error_id(self):
        import main
        log_path = main.LOGS / "app.log"
        with patch("api.documents_router.encode_documents", side_effect=RuntimeError("model unavailable")):
            response = self.client.post("/api/documents/import", headers=self.login("admin", "Admin123!"),
                                        files={"files": ("failure.txt", b"diagnostic test", "text/plain")})
        self.assertEqual(response.status_code, 500)
        request_id = response.json()["detail"].split("：")[-1]
        log = log_path.read_text(encoding="utf-8")
        self.assertIn(request_id, log)
        self.assertIn("Document import failed", log)

    def test_remote_candidates_are_authorized_before_rerank_and_generation(self):
        import main
        with main.db() as con:
            allowed_id = con.execute(
                "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.title='差旅报销标准'"
            ).fetchone()["id"]
            restricted_id = con.execute(
                "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.title='高管薪酬与股权激励细则'"
            ).fetchone()["id"]
        os.environ["KB_RAG_BACKEND"] = "milvus"

        def check_rerank(question, documents):
            self.assertEqual(len(documents), 1)
            self.assertNotIn("薪酬", documents[0]["content"])
            return [{**documents[0], "score": 0.9}]

        with patch("main.rag_service.upsert_vectors"), \
             patch("main.rag_service.hybrid_search",
                   side_effect=[[allowed_id, restricted_id], [allowed_id, restricted_id]]), \
             patch("main.rag_service.generate_hypothetical_document", return_value="差旅报销规定"), \
             patch("main.rag_service.rerank", side_effect=check_rerank), \
             patch("main.rag_service.generate_answer", return_value="出差后十个工作日内提交报销单 [1]"):
            response = self.client.post(
                "/api/chat", headers=self.login("staff", "Staff123!"),
                json={"question": "差旅报销流程是什么"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["restricted"])
        self.assertEqual(len(response.json()["citations"]), 1)
        self.assertNotIn("薪酬", response.json()["answer"])

    def test_markdown_split_keeps_heading_context(self):
        import main
        chunks = main.split_text(
            "# HAK 180\n## 安全操作\n设备运行前检查防护罩。操作结束后切断电源。", size=80)
        self.assertTrue(chunks)
        self.assertTrue(all("# HAK 180" in chunk and "## 安全操作" in chunk for chunk in chunks))

    def test_imported_document_is_retrieved_from_multiple_chunks(self):
        headers = self.login("admin", "Admin123!")
        response = self.client.post("/api/documents/import", headers=headers,
                                    files={"files": ("manual.txt", b"Device model HAK 180 supports foil printing.\n\nThe device uses a temperature control panel.", "text/plain")})
        self.assertEqual(response.status_code, 200)
        result = self.client.post("/api/chat", headers=headers, json={"question": "temperature control panel"}).json()
        self.assertTrue(result["citations"])
        self.assertIn("temperature", result["answer"].lower())
        self.assertLess(len(result["answer"]), 500)

    def test_old_roles_table_migrates_permissions_column(self):
        import main
        original_path = main.DB_PATH
        legacy_path = os.path.join(self.temp.name, "legacy.db")
        try:
            con = sqlite3.connect(legacy_path)
            try:
                con.execute("CREATE TABLE roles(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,built_in INTEGER NOT NULL DEFAULT 0)")
            finally:
                con.close()
            main.DB_PATH = legacy_path
            main.init_db()
            con = sqlite3.connect(legacy_path)
            try:
                columns = {row[1] for row in con.execute("PRAGMA table_info(roles)")}
                self.assertIn("permissions", columns)
                self.assertEqual(con.execute("SELECT count(*) FROM roles").fetchone()[0], 4)
                self.assertEqual(json.loads(con.execute(
                    "SELECT permissions FROM roles WHERE id='user'").fetchone()[0]), ["chat.access"])
                con.execute("UPDATE roles SET permissions='[]'")
                con.commit()
            finally:
                con.close()
            main.init_db()
            con = sqlite3.connect(legacy_path)
            try:
                self.assertEqual(json.loads(con.execute(
                    "SELECT permissions FROM roles WHERE id='user'").fetchone()[0]), ["chat.access"])
                con.execute("UPDATE roles SET permissions='[\"dashboard.view\"]' WHERE id='user'")
                con.commit()
            finally:
                con.close()
            main.init_db()
            con = sqlite3.connect(legacy_path)
            try:
                self.assertEqual(json.loads(con.execute(
                    "SELECT permissions FROM roles WHERE id='user'").fetchone()[0]), ["dashboard.view"])
            finally:
                con.close()
        finally:
            main.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
