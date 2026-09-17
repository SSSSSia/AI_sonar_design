# -*- coding: utf-8 -*-
"""
GraphRAG 索引构建脚本
====================

运行方式:
    cd D:/CODE_FILE/CODE_PYTHON/Sonar_cad_design
    python -m graphrag_service.build_index
"""

import os
import sys
from pathlib import Path


def _load_env(env_path):
    """从 .env 文件加载环境变量（不覆盖已存在的变量）"""
    env_path = Path(env_path)
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and key not in os.environ:
                    os.environ[key] = value


# 加载 .env（先加载项目根目录 .env，再加载知识库本地 .env）
_load_env(Path(__file__).parent.parent / ".env")
_load_env(Path(__file__).parent / "kb" / ".env")

from .sdk import GraphRAGSDK

# 知识库根目录
KB_ROOT = (Path(__file__).parent / "kb").resolve()


def main():
    """构建知识库索引主函数"""
    print("=" * 80)
    print("拖曳线列阵声呐 CAD 知识库 - GraphRAG 索引构建")
    print("=" * 80)
    print(f"知识库路径: {KB_ROOT}")
    print(f"输入文件: {KB_ROOT / 'input' / 'towed_array_cad_knowledge.txt'}")
    print(f"配置文件: {KB_ROOT / 'settings.yaml'}")
    print("=" * 80)

    # 检查输入文件
    input_file = KB_ROOT / "input" / "towed_array_cad_knowledge.txt"
    if not input_file.exists():
        print(f"\n[错误] 输入文件不存在: {input_file}")
        print("请确保知识库文本已放置在 kb/input/ 目录下。")
        sys.exit(1)

    print(f"\n[准备] 输入文件检查通过")
    print(f"{'=' * 80}")
    print("[开始] 构建知识图谱索引...")
    print(f"{'=' * 80}\n")

    # 创建 SDK 实例并构建索引
    sdk = GraphRAGSDK(root_dir=str(KB_ROOT))
    result = sdk.build_index_sync(verbose=True, is_update=False)

    print(f"\n{'=' * 80}")
    if result.get("success"):
        print("[成功] 索引构建完成!")
        workflows = result.get("workflows", [])
        workflow_names = [w if isinstance(w, str) else w.get("name", str(w)) for w in workflows]
        print(f"完成的工作流: {', '.join(workflow_names)}")
        print(f"\n索引产物位于: {KB_ROOT / 'output'}")
        print(f"向量数据库: {KB_ROOT / 'output' / 'lancedb'}")
    else:
        print(f"[失败] 索引构建失败: {result.get('message')}")
        sys.exit(1)

    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
