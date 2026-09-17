"""知识底座：文档预处理管线（宪法 §6）+ GraphRAG/Neo4j/Milvus 封装 + 三级降级检索。

检索三级降级（A-14）：图谱检索（local）→ 向量检索（basic）→ 文本关键词，
任一级失败降级不报错中断。
阶段 1 T1.6 从旧仓库迁移 graphrag_service/sdk.py + service.py（★ 直接迁移，异步包装）。
"""
