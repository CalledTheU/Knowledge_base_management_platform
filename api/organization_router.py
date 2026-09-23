# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: 2026-09-23

import json
import sqlite3
import uuid

from fastapi import Depends, FastAPI, HTTPException

from schema.organization_schema import DepartmentInput, RoleInput, UserCreate, UserStatus, UserUpdate


def register_router(app: FastAPI, *, db, current_user, require_permission, require_any_permission,
                    row_user, password_hash, permissions):
    require_organization = require_permission("organization.manage")
    require_directory = require_any_permission("organization.manage", "knowledge.manage")

    def validate_permissions(values):
        if len(values) != len(set(values)) or not set(values) <= permissions:
            raise HTTPException(400, "角色功能权限无效")

    def has_organization_admin(con, *, replace_user=None, replacement_roles=None, exclude_user=None):
        for account in con.execute("SELECT id,roles FROM users WHERE active=1"):
            if account["id"] == exclude_user:
                continue
            role_ids = replacement_roles if account["id"] == replace_user else json.loads(account["roles"])
            effective = set()
            for role_id in role_ids:
                role = con.execute("SELECT permissions FROM roles WHERE id=?", (role_id,)).fetchone()
                if role:
                    effective.update(json.loads(role["permissions"]))
            if "organization.manage" in effective:
                return True
        return False
    @app.get("/api/departments")
    def departments(user=Depends(require_directory)):
        with db() as con:
            return [dict(r) for r in con.execute("SELECT * FROM departments ORDER BY name")]

    def validate_parent(con, parent_id, department_id=None):
        if parent_id is None:
            return
        if parent_id == department_id:
            raise HTTPException(400, "部门不能将自身设为上级")
        if not con.execute("SELECT 1 FROM departments WHERE id=?", (parent_id,)).fetchone():
            raise HTTPException(400, "上级部门不存在")
        ancestor = parent_id
        while ancestor:
            if ancestor == department_id:
                raise HTTPException(400, "不能将部门移动到其下级部门")
            row = con.execute("SELECT parent_id FROM departments WHERE id=?", (ancestor,)).fetchone()
            ancestor = row["parent_id"] if row else None

    @app.post("/api/departments")
    def add_department(body: DepartmentInput, user=Depends(require_organization)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "部门名称不能为空")
        department_id = str(uuid.uuid4())
        with db() as con:
            validate_parent(con, body.parent_id)
            con.execute("INSERT INTO departments VALUES(?,?,?)", (department_id, name, body.parent_id))
        return {"id": department_id, "name": name, "parent_id": body.parent_id}

    @app.put("/api/departments/{department_id}")
    def update_department(department_id: str, body: DepartmentInput, user=Depends(require_organization)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "部门名称不能为空")
        with db() as con:
            if not con.execute("SELECT 1 FROM departments WHERE id=?", (department_id,)).fetchone():
                raise HTTPException(404, "部门不存在")
            validate_parent(con, body.parent_id, department_id)
            con.execute("UPDATE departments SET name=?,parent_id=? WHERE id=?",
                        (name, body.parent_id, department_id))
        return {"ok": True}

    @app.delete("/api/departments/{department_id}")
    def delete_department(department_id: str, user=Depends(require_organization)):
        with db() as con:
            if not con.execute("SELECT 1 FROM departments WHERE id=?", (department_id,)).fetchone():
                raise HTTPException(404, "部门不存在")
            if con.execute("SELECT 1 FROM departments WHERE parent_id=? LIMIT 1", (department_id,)).fetchone():
                raise HTTPException(409, "请先移动或删除下级部门")
            if con.execute("SELECT 1 FROM users WHERE department_id=? LIMIT 1", (department_id,)).fetchone():
                raise HTTPException(409, "部门仍有关联用户")
            if con.execute("SELECT 1 FROM acl WHERE kind='department' AND subject=? LIMIT 1", (department_id,)).fetchone():
                raise HTTPException(409, "部门仍被知识权限引用")
            con.execute("DELETE FROM departments WHERE id=?", (department_id,))
        return {"ok": True}

    @app.get("/api/roles")
    def roles(user=Depends(require_directory)):
        with db() as con:
            return [{**dict(r), "permissions": json.loads(r["permissions"])} for r in
                    con.execute("SELECT id,name,built_in,permissions FROM roles ORDER BY built_in DESC,name")]

    @app.post("/api/roles")
    def add_role(body: RoleInput, user=Depends(require_organization)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "角色名称不能为空")
        validate_permissions(body.permissions)
        role_id = str(uuid.uuid4())
        try:
            with db() as con:
                con.execute("INSERT INTO roles VALUES(?,?,0,?)", (role_id, name, json.dumps(body.permissions)))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "角色名称已存在")
        return {"id": role_id, "name": name, "built_in": 0}

    @app.put("/api/roles/{role_id}")
    def update_role(role_id: str, body: RoleInput, user=Depends(require_organization)):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "角色名称不能为空")
        validate_permissions(body.permissions)
        try:
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                role = con.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone()
                if not role:
                    raise HTTPException(404, "角色不存在")
                con.execute("UPDATE roles SET name=?,permissions=? WHERE id=?",
                            (name, json.dumps(body.permissions), role_id))
                if not has_organization_admin(con):
                    raise HTTPException(409, "至少保留一个有效的组织管理员角色")
        except sqlite3.IntegrityError:
            raise HTTPException(409, "角色名称已存在")
        return {"ok": True}

    @app.delete("/api/roles/{role_id}")
    def delete_role(role_id: str, user=Depends(require_organization)):
        with db() as con:
            role = con.execute("SELECT built_in FROM roles WHERE id=?", (role_id,)).fetchone()
            if not role:
                raise HTTPException(404, "角色不存在")
            if role["built_in"]:
                raise HTTPException(409, "系统内置角色不能删除")
            if any(role_id in json.loads(r["roles"]) for r in con.execute("SELECT roles FROM users")):
                raise HTTPException(409, "角色仍分配给用户")
            if con.execute("SELECT 1 FROM acl WHERE kind='role' AND subject=? LIMIT 1", (role_id,)).fetchone():
                raise HTTPException(409, "角色仍被知识权限引用")
            con.execute("DELETE FROM roles WHERE id=?", (role_id,))
        return {"ok": True}

    @app.get("/api/users")
    def users(user=Depends(require_organization)):
        with db() as con:
            rows = con.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [row_user(r) for r in rows]

    @app.post("/api/users")
    def add_user(body: UserCreate, user=Depends(require_organization)):
        username, name = body.username.strip(), body.display_name.strip()
        if not username or not name:
            raise HTTPException(400, "用户名和显示名称不能为空")
        salt, digest = password_hash(body.password)
        user_id = str(uuid.uuid4())
        try:
            with db() as con:
                if body.department_id and not con.execute(
                        "SELECT 1 FROM departments WHERE id=?", (body.department_id,)).fetchone():
                    raise HTTPException(400, "部门不存在")
                roles = list(dict.fromkeys(body.roles or ["user"]))
                known = {r["id"] for r in con.execute("SELECT id FROM roles")}
                if not set(roles) <= known:
                    raise HTTPException(400, "包含未定义角色")
                con.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,1)",
                            (user_id, username, name, salt, digest, body.department_id, json.dumps(roles)))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "用户名已存在")
        return {"id": user_id, "username": username}

    @app.put("/api/users/{user_id}")
    def update_user(user_id: str, body: UserUpdate, user=Depends(require_organization)):
        name = body.display_name.strip()
        if not name:
            raise HTTPException(400, "显示名称不能为空")
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                raise HTTPException(404, "用户不存在")
            if body.department_id and not con.execute(
                    "SELECT 1 FROM departments WHERE id=?", (body.department_id,)).fetchone():
                raise HTTPException(400, "部门不存在")
            roles = list(dict.fromkeys(body.roles))
            known = {r["id"] for r in con.execute("SELECT id FROM roles")}
            if not roles or not set(roles) <= known:
                raise HTTPException(400, "用户角色无效")
            if user_id == user["id"] and not ({"system_admin", "knowledge_admin"} & set(roles)):
                raise HTTPException(400, "不能移除自己的全部管理员角色")
            con.execute("UPDATE users SET display_name=?,department_id=?,roles=? WHERE id=?",
                        (name, body.department_id, json.dumps(roles), user_id))
            if not has_organization_admin(con):
                raise HTTPException(409, "至少保留一个有效的组织管理员账号")
        return {"ok": True}

    @app.patch("/api/users/{user_id}/status")
    def update_user_status(user_id: str, body: UserStatus, user=Depends(require_organization)):
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT active,roles FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise HTTPException(404, "用户不存在")
            if not body.active and row["active"]:
                if user_id == user["id"]:
                    raise HTTPException(409, "不能停用当前登录账号")
                if "organization.manage" in user["permissions"] and not has_organization_admin(
                        con, exclude_user=user_id):
                    raise HTTPException(409, "不能停用最后一个有效管理员")
            con.execute("UPDATE users SET active=? WHERE id=?", (body.active, user_id))
        return {"ok": True, "active": body.active}
