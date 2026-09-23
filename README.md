# 知识库管理平台

FastAPI + SQLite 的本地可运行首版，提供登录、部门/账号、知识导入与四维 ACL、权限过滤问答、引用审计、运营指标、FAQ 审核缓存和知识缺口列表。首次启动自动创建数据和演示账号。

## 启动

在 VS Code 打开本目录，在终端执行：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

访问 <http://127.0.0.1:8000>。Swagger API 文档在 <http://127.0.0.1:8000/docs>。

演示账号：`admin / Admin123!`（平台管理员）、`finance / Finance123!`（财务部门）、`staff / Staff123!`（业务部门）。登录后可切换用户验证部门/角色权限。首次启动生成的 SQLite 数据在 `data/knowledge.db`；上传源文件在 `data/uploads/`。可通过 `KB_DB_PATH` 指定数据库文件。

支持 PDF、DOCX、Markdown、TXT；文件最大 20MB，单次最多 30 个。导入知识默认仅当前管理员可见，需在台账中配置 ACL 后开放。权限实体为 `global`、`department`、`role`、`user`，符合任一实体即可读取，空 ACL 默认拒绝访问。

## 当前版本边界

- 检索先用 SQLite 本地词项匹配，尚未接入旧项目的 Milvus/BGE/LLM；回答内容来自授权切片摘录，保证无模型配置时可以演示完整权限流程。
- 运营 Token 为文本长度估算；FAQ 候选按完全相同的问题聚合，发布后精确匹配缓存。
- 登录使用随机 Bearer token 和 SQLite 会话，适用于本地演示；生产环境应配置 HTTPS、限流、密钥轮换和更完整的账号生命周期控制。
- 导入在请求内同步解析；超大规模批量、后台进度、PDF OCR、文件夹递归导入、流式生成和定时语义聚类尚未实现。

运行检查：`python -m unittest discover -s tests`。
