# Project Instructions

## Project context

- This file is intentionally at the repository root. Project-wide instructions belong in the nearest `AGENTS.md`; project Skills belong under `.agents/skills/<skill-name>/SKILL.md`.
- Full product requirements: `data/知识库管理平台.md`.
- Persistent development handoff: `data/start_with_me.md`. At each meaningful project stage boundary, keep one concise history entry per completed stage and a current-stage snapshot; do not duplicate the full requirements.
- Follow the existing `main.py`, `api/`, `schema/`, `front/`, and `tests/` structure. This project currently uses FastAPI and SQLite; do not introduce a new architecture or dependency without a requirement.

## Tracked project guidance

- Keep `data/知识库管理平台-总需求文档.md.md`, `data/start_with_me.md`, and `.agents/skills/` in version control.
- Runtime SQLite databases and imported source files under `data/uploads/` are generated/user data and must remain untracked.

## Python files

Every newly created `.py` file must begin with:

```python
# Author: WangLei
# Email: WangLei1578@outlook.com
# Date: YYYY-MM-DD
```

Use the actual creation date in `yyyy-MM-dd` format. For new `.java` files, use:

```java
/**
 * @author WangLei
 * @email WangLei1578@outlook.com
 * @date yyyy-MM-dd
 */
```

## Run and verify

- Start locally: `python main.py` (after installing `requirements.txt` in the project virtual environment).
- Tests: `.venv\Scripts\python.exe -m unittest discover -s tests -v`.
- Frontend syntax check when `front/app.js` changes: `node --check front/app.js`.
- For database changes, verify both a new database and migration from an existing database when practical.
