# -*- coding: utf-8 -*-
"""
GraphRAG SDK 模块
=================

提供 Python SDK，用于在 Python 代码中直接调用 GraphRAG 功能。
"""

# ── 日志配置必须在所有第三方 import 之前 ──
# litellm 在 import 时就会输出 WARNING，必须先配好才能拦截
import logging
from pathlib import Path

# v2 迁移（T1.6）：日志落 py-engine/logs/knowledge.log，不再写旧仓库 output/
_log_file = Path(__file__).parent.parent / "logs" / "knowledge.log"
_log_file.parent.mkdir(parents=True, exist_ok=True)

# 使用显式 FileHandler 指定 UTF-8 编码，避免 Windows 上默认 GBK 导致中文乱码
_file_handler = logging.FileHandler(
    str(_log_file), mode="a", encoding="utf-8"
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_file_handler],
)
logger = logging.getLogger(__name__)

# 控制台只显示 WARNING 及以上
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)
logging.root.addHandler(_console_handler)

# litellm 自带 StreamHandler，设 ERROR + 清除它的 handler
for lit in ("litellm", "LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
    ll = logging.getLogger(lit)
    ll.setLevel(logging.ERROR)
    ll.handlers.clear()
    ll.propagate = False

# 其余第三方库：asyncio 设 CRITICAL（抑制 Unclosed session/connector），
# 其余设 WARNING
for noisy in ("httpx", "httpcore", "graphrag_storage", "graphrag.query",
              "graphrag_llm", "openai", "urllib3", "asyncio"):
    logging.getLogger(noisy).setLevel(
        logging.CRITICAL if noisy == "asyncio" else logging.WARNING
    )

# graphrag.query 子模块设 ERROR（过滤重复 WARNING：No community records / Reached token limit）
for noisy in ("graphrag.query.context_builder.community_context",
              "graphrag.query.structured_search.local_search.mixed_context"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

# ── 以下才是第三方 import ──
import asyncio
from typing import Any, Optional

import graphrag.api as api
from graphrag.cli.initialize import initialize_project_at
from graphrag.config.load_config import load_config
from graphrag.config.enums import IndexingMethod
from graphrag_storage import create_storage
from graphrag_storage.tables.table_provider_factory import create_table_provider
from graphrag.data_model.data_reader import DataReader


class GraphRAGSDK:
    """GraphRAG SDK - 提供简单的 Python 接口调用 GraphRAG 功能

    这个类封装了 GraphRAG 的核心功能，可以在 Agent 等模块中直接使用。

    使用示例:
        sdk = GraphRAGSDK(root_dir="./graphrag_project")

        # 初始化项目
        sdk.init_project()

        # 构建索引
        sdk.build_index_sync()

        # 查询
        answer = sdk.ask("GraphRAG 是什么？", method="global")
    """

    def __init__(self, root_dir: str = "./graphrag_project"):
        """初始化 SDK

        Args:
            root_dir: GraphRAG 项目根目录（包含 settings.yaml）
        """
        self.root_dir = Path(root_dir).resolve()
        self.config = None
        self._cache = {}  # 缓存加载的数据

    # ============================================================
    # 项目管理
    # ============================================================

    def init_project(self, force: bool = False) -> dict:
        """初始化项目目录和配置文件

        Args:
            force: 是否覆盖已有文件

        Returns:
            初始化结果
        """
        try:
            self.root_dir.mkdir(parents=True, exist_ok=True)
            (self.root_dir / "input").mkdir(parents=True, exist_ok=True)

            initialize_project_at(path=self.root_dir, force=force)

            return {
                "success": True,
                "message": f"项目初始化成功: {self.root_dir}",
                "root_dir": str(self.root_dir),
                "input_dir": str(self.root_dir / "input")
            }
        except Exception as e:
            logger.error(f"初始化项目失败: {e}")
            return {"success": False, "message": str(e)}

    def _load_config(self):
        """加载配置"""
        if self.config is None:
            self.config = load_config(root_dir=self.root_dir)
        return self.config

    # ============================================================
    # 索引构建
    # ============================================================

    async def build_index(
        self,
        is_update: bool = False,
        verbose: bool = True
    ) -> dict:
        """构建或更新知识图谱索引

        Args:
            is_update: 是否为增量更新
            verbose: 是否输出详细日志

        Returns:
            构建结果
        """
        try:
            config = self._load_config()
            results = await api.build_index(
                config=config,
                method=IndexingMethod.Standard,
                is_update_run=is_update,
                verbose=verbose,
            )

            # 清除缓存
            self._cache.clear()

            # 检查各工作流状态
            workflow_status = []
            has_errors = False
            for r in results:
                errors = getattr(r, 'errors', None) or []
                status = "ok" if not errors else f"errors: {errors}"
                workflow_status.append({"workflow": r.workflow, "status": status})
                if errors:
                    has_errors = True

            return {
                "success": not has_errors,
                "message": "索引构建成功" if not has_errors else "索引构建完成，部分工作流有错误",
                "workflows": workflow_status
            }
        except Exception as e:
            logger.error(f"索引构建失败: {e}")
            return {"success": False, "message": str(e)}

    def build_index_sync(self, is_update: bool = False, verbose: bool = True) -> dict:
        """同步版本的索引构建"""
        return asyncio.run(self.build_index(is_update, verbose))

    # ============================================================
    # 数据加载
    # ============================================================

    async def _load_data(self, force_reload: bool = False):
        """加载索引数据"""
        if not force_reload and "data" in self._cache:
            return self._cache["data"]

        config = self._load_config()
        storage_obj = create_storage(config.output_storage)
        table_provider = create_table_provider(config.table_provider, storage=storage_obj)
        reader = DataReader(table_provider)

        # 加载必需的表
        entities = await reader.entities()
        communities = await reader.communities()
        community_reports = await reader.community_reports()
        text_units = await reader.text_units()
        relationships = await reader.relationships()

        # 加载可选的表
        covariates = None
        if await table_provider.has("covariates"):
            covariates = await reader.covariates()

        self._cache["data"] = {
            "config": config,
            "entities": entities,
            "communities": communities,
            "community_reports": community_reports,
            "text_units": text_units,
            "relationships": relationships,
            "covariates": covariates
        }

        return self._cache["data"]

    # ============================================================
    # 查询功能
    # ============================================================

    async def query_global(
        self,
        query: str,
        community_level: int = 2,
        dynamic_community_selection: bool = False,
        response_type: str = "Multiple Paragraphs"
    ) -> dict:
        """全局搜索 - 基于社区报告的整体性问答

        适合回答关于整个数据集的概括性问题。

        Args:
            query: 查询问题
            community_level: 社区层级
            dynamic_community_selection: 动态社区选择
            response_type: 响应类型

        Returns:
            {"success": bool, "response": str, "context": dict}
        """
        try:
            data = await self._load_data()

            response, context_data = await api.global_search(
                config=data["config"],
                entities=data["entities"],
                communities=data["communities"],
                community_reports=data["community_reports"],
                community_level=community_level,
                dynamic_community_selection=dynamic_community_selection,
                response_type=response_type,
                query=query,
            )

            return {
                "success": True,
                "response": response,
                "context": context_data
            }
        except Exception as e:
            logger.error(f"全局搜索失败: {e}")
            return {"success": False, "error": str(e)}

    async def query_local(
        self,
        query: str,
        community_level: int = 2,
        response_type: str = "Multiple Paragraphs"
    ) -> dict:
        """本地搜索 - 基于实体和关系的精细问答

        适合回答关于具体实体的详细问题。

        Args:
            query: 查询问题
            community_level: 社区层级
            response_type: 响应类型

        Returns:
            {"success": bool, "response": str, "context": dict}
        """
        try:
            data = await self._load_data()

            response, context_data = await api.local_search(
                config=data["config"],
                entities=data["entities"],
                communities=data["communities"],
                community_reports=data["community_reports"],
                text_units=data["text_units"],
                relationships=data["relationships"],
                covariates=data["covariates"],
                community_level=community_level,
                response_type=response_type,
                query=query,
            )

            return {
                "success": True,
                "response": response,
                "context": context_data
            }
        except Exception as e:
            logger.error(f"本地搜索失败: {e}")
            return {"success": False, "error": str(e)}

    async def query_drift(
        self,
        query: str,
        community_level: int = 2,
        response_type: str = "Multiple Paragraphs"
    ) -> dict:
        """DRIFT 搜索 - 综合搜索

        适合回答需要结合局部实体和全局社区信息的混合问题。

        Args:
            query: 查询问题
            community_level: 社区层级
            response_type: 响应类型

        Returns:
            {"success": bool, "response": str, "context": dict}
        """
        try:
            data = await self._load_data()

            response, context_data = await api.drift_search(
                config=data["config"],
                entities=data["entities"],
                communities=data["communities"],
                community_reports=data["community_reports"],
                text_units=data["text_units"],
                relationships=data["relationships"],
                community_level=community_level,
                response_type=response_type,
                query=query,
            )

            return {
                "success": True,
                "response": response,
                "context": context_data
            }
        except Exception as e:
            logger.error(f"DRIFT 搜索失败: {e}")
            return {"success": False, "error": str(e)}

    async def query_basic(self, query: str, response_type: str = "Multiple Paragraphs") -> dict:
        """基础搜索 - 简单语义搜索

        适合简单的关键词/语义匹配搜索。

        Args:
            query: 查询问题
            response_type: 响应类型（默认 "Multiple Paragraphs"）

        Returns:
            {"success": bool, "response": str, "context": dict}
        """
        try:
            data = await self._load_data()

            response, context_data = await api.basic_search(
                config=data["config"],
                text_units=data["text_units"],
                response_type=response_type,
                query=query,
            )

            return {
                "success": True,
                "response": response,
                "context": context_data
            }
        except Exception as e:
            logger.error(f"基础搜索失败: {e}")
            return {"success": False, "error": str(e)}

    # ============================================================
    # 同步版本方法（用于非异步环境）
    # ============================================================

    def query_global_sync(self, query: str, **kwargs) -> dict:
        """同步版本的全局搜索"""
        return asyncio.run(self.query_global(query, **kwargs))

    def query_local_sync(self, query: str, **kwargs) -> dict:
        """同步版本的本地搜索"""
        return asyncio.run(self.query_local(query, **kwargs))

    def query_drift_sync(self, query: str, **kwargs) -> dict:
        """同步版本的 DRIFT 搜索"""
        return asyncio.run(self.query_drift(query, **kwargs))

    def query_basic_sync(self, query: str, **kwargs) -> dict:
        """同步版本的基础搜索"""
        return asyncio.run(self.query_basic(query, **kwargs))

    # ============================================================
    # 便捷方法
    # ============================================================

    def ask(self, question: str, method: str = "global", **kwargs) -> str:
        """便捷的问答方法（同步）

        Args:
            question: 问题
            method: 查询方法 (global/local/drift/basic)
            **kwargs: 其他查询参数

        Returns:
            答案文本
        """
        method_map = {
            "global": self.query_global_sync,
            "local": self.query_local_sync,
            "drift": self.query_drift_sync,
            "basic": self.query_basic_sync
        }

        if method not in method_map:
            raise ValueError(f"无效的查询方法: {method}")

        result = method_map[method](question, **kwargs)

        if result.get("success"):
            return result.get("response", "")
        else:
            raise RuntimeError(f"查询失败: {result.get('error')}")

    def reload_cache(self):
        """重新加载数据缓存"""
        self._cache.clear()

    # ============================================================
    # 批量查询（多查询共享一个事件循环）
    # ============================================================

    def query_batch_sync(
        self,
        queries: list,
        method: str = "local",
        **kwargs,
    ) -> list:
        """批量并发查询（共享单个事件循环）

        相比逐条调用 query_*_sync()，此方法用 asyncio.gather()
        在一个事件循环内并发执行所有查询，避免重复创建事件循环的开销。

        Args:
            queries: 查询字符串列表
            method: 查询方法 ("local", "global", "drift", "basic")

        Returns:
            结果字典列表，与输入 queries 顺序一致
        """
        if not queries:
            return []

        method_map = {
            "local": self.query_local,
            "global": self.query_global,
            "drift": self.query_drift,
            "basic": self.query_basic,
        }
        async_fn = method_map.get(method, self.query_local)

        async def _run_batch():
            tasks = [async_fn(q, **kwargs) for q in queries]
            return await asyncio.gather(*tasks, return_exceptions=True)

        results = asyncio.run(_run_batch())

        processed = []
        for r in results:
            if isinstance(r, Exception):
                processed.append({"success": False, "error": str(r)})
            else:
                processed.append(r)
        return processed

    def query_batch_sync_with_fallback(self, queries: list, **kwargs) -> list:
        """批量查询（带逐条降级：local_search → basic_search）

        对每条查询先尝试 local_search，失败则降级到 basic_search。
        所有查询在同一事件循环内并发执行。

        Args:
            queries: 查询字符串列表

        Returns:
            结果字典列表，与输入 queries 顺序一致
        """
        if not queries:
            return []

        async def _query_with_fallback(query: str) -> dict:
            """单条查询：先 local，失败则 basic"""
            result = await self.query_local(query, **kwargs)
            if result.get("success"):
                return result
            logger.debug(
                "local_search 失败，降级到 basic_search: %s",
                result.get("error", ""),
            )
            try:
                result = await self.query_basic(query, **kwargs)
            except Exception as e:
                result = {"success": False, "error": str(e)}
            return result

        async def _run_batch():
            tasks = [_query_with_fallback(q) for q in queries]
            return await asyncio.gather(*tasks, return_exceptions=True)

        results = asyncio.run(_run_batch())

        processed = []
        for r in results:
            if isinstance(r, Exception):
                processed.append({"success": False, "error": str(r)})
            else:
                processed.append(r)
        return processed


# ============================================================
# 单例模式
# ============================================================

_instance: Optional[GraphRAGSDK] = None


def get_sdk(root_dir: str = "./graphrag_project") -> GraphRAGSDK:
    """获取 GraphRAG SDK 单例

    Args:
        root_dir: 项目根目录

    Returns:
        GraphRAGSDK 实例
    """
    global _instance
    if _instance is None or _instance.root_dir != Path(root_dir).resolve():
        _instance = GraphRAGSDK(root_dir)
    return _instance
