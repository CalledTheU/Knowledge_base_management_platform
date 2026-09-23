---
name: project-stage-recap
description: At the end of each meaningful development phase in this project, report completed and incomplete goals, the next phase, and concrete developer verification steps with results. Use for implementation work in this project; skip standalone explanations and trivial edits that do not complete a phase.
---

# Project Stage Recap

Use this skill when implementing or extending the Knowledge Base Management Platform. Treat a stage as a coherent deliverable, such as a user workflow, backend capability, UI module, integration, or milestone. Do not interrupt work to recap every small edit; recap when a stage is complete, when work must pause, or when a blocker prevents the next meaningful step.

The project's complete development requirements are in `data/知识库管理平台.md`. Read the relevant requirements before implementation and use its scope and acceptance criteria to track project progress; do not treat a stage recap as proof that the full project is complete.

At every stage boundary, update `data/start_with_me.md` with a concise current-state snapshot and a compact history. Keep the current stage detailed enough to continue (completed outcomes, important remaining requirements/limitations, verification results, next stage/acceptance condition). Record each completed past stage as one short history entry containing its goal, main outcome, and verification result; do not copy old full recaps or repeat the complete project backlog, which remains in `data/知识库管理平台.md`. Keep the file synchronized with the recap reported to the developer. This file is the persistent handoff; create it if missing.

At each stage boundary, give the developer a concise, evidence-based handoff covering:

1. **Stage and goal**: identify the stage and the user-visible or technical outcome it was intended to deliver.
2. **Completed**: list only outcomes implemented in this stage. Link important files and mention relevant behavior, API, or data changes.
3. **Not completed**: list remaining acceptance criteria, known limitations, blockers, and anything deferred. Distinguish intentional deferrals from failures.
4. **Verification**: give exact commands or UI steps a developer can run, the expected result, and the actual result observed. Include relevant test names and results. If a check was not run, say so and why; never imply it passed.
5. **Next stage**: name the next coherent goal and its main acceptance condition. Keep it aligned with the user's requested scope; do not silently expand the project.

For verification, prefer the smallest meaningful checks for the changed behavior: focused automated tests, lint/type/syntax checks when configured, and a direct UI/API workflow when the change affects users. Explain any environment prerequisite such as starting the server, using a demo account, or configuring a service. For security-sensitive behavior, include both an allowed case and a denied case where practical.

Keep recaps short enough to scan but detailed enough that a developer can independently decide whether the stage is done. Use this format when useful:

```text
阶段：<名称与目标>
已完成：<实现结果>
未完成：<遗留项、限制或阻塞；没有则写“无”>
验证：<命令/操作> -> 预期：<结果>；实际：<结果或未运行原因>
下一阶段：<目标与完成条件>
```

If the full project request is not complete, do not present the stage as overall project completion. Preserve enough current context to continue without rediscovery. Keep history compact: one entry per stage, and do not duplicate details already captured in the current snapshot or total requirements document.
