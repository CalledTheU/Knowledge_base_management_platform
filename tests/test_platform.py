# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-23

import os
import json
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient


class PlatformFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["KB_DB_PATH"] = os.path.join(self.temp.name, "test.db")
        import main
        main.DB_PATH = os.environ["KB_DB_PATH"]
        self.app = main.app
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()
        os.environ.pop("KB_DB_PATH", None)

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

    def test_miss_creates_gap(self):
        finance = self.login("finance", "Finance123!")
        self.client.post("/api/chat", headers=finance, json={"question": "火星基地供水系统方案"})
        admin = self.login("admin", "Admin123!")
        gaps = self.client.get("/api/gaps", headers=admin).json()
        self.assertTrue(any(x["question"] == "火星基地供水系统方案" for x in gaps))

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
