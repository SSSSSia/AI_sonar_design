# AI Sonar Design（Sonar-CAD v2）

声呐装备智能辅助设计系统 v2 —— 知识库 + 方案/CAD 代码生成工作流的前后端分离 demo。

> **开发前必读**：[`docs/00_开发约束与协作规范.md`](docs/00_开发约束与协作规范.md)（宪法文档：架构硬约束、文档驱动流程、Git 纪律、裁剪决策）。

## 一句话定位

以参数化 CAD 设计为核心、知识图谱增强检索为依据：需求智能解析 → 技术方案生成 → 参数化 CAD 建模出图 → 设计合规校验 → 知识沉淀复用，全程带审阅关与溯源。

## 技术基线（demo 裁剪版）

| 层 | 选型 |
|---|---|
| 前端 | React + TypeScript + Vite + Ant Design |
| 后端 | Python 3.12 + FastAPI（单后端，platform/ 与 agents/ 物理分层） |
| 知识库 | GraphRAG + Chroma（文档预处理入库管线） |
| CAD | CadQuery 子进程沙箱 → STEP |
| 业务库 | SQLite（SQLAlchemy，可切 PostgreSQL） |

## 开发约定（速记）

- 文档先行：无文档不开工；文档与代码同一 commit 同步。
- 小步提交：每完成一个子任务即 commit + push，Conventional Commits。
- 架构硬约束 A-01~A-16 不可违背（见宪法文档 §3）。
- UI 改动先 mockup 确认再落地；蓝色仅用于交互语义。
- 打 tag 前走发布检查清单（宪法文档附录 A）。

## 设计文档来源

权威设计文档位于旧项目 `../Sonar_cad_design/docs/项目设计相关/`，优先级与裁决规则见宪法文档 §1。
