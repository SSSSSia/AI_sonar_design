"""Sonar-CAD v2 算力层（py-engine）。

Python 3.12 + FastAPI，承担 A1–A5 Agent / CAD 执行 / 约束求解 / 检索（D-01）。
worker 无状态：消费任务、执行、结果写共享存储（PG/MinIO/Neo4j），状态经队列回传（P3 §8.3）。

目录（宪法附录 C）：
- api/         任务消费入口与内部接口
- agents/      A1–A5（每 Agent 一目录，含独立调试入口）
- knowledge/   文档预处理管线 + GraphRAG/Neo4j/Milvus 封装
- solver/      约束求解底座（等式/区间/军标接入框架）
- cad_engine/  CadQuery 沙箱执行
"""
