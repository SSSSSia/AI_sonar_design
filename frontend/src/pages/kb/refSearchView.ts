/**
 * T17.6 检索呈现纯函数（混合 level 徽标/融合轨迹/图谱实体标记；供 vitest）。
 * 契约=ADR-016 §2/§3：hits.paths 为融合级命中路名（["bm25","vector"]）、
 * hits.kind="graph" 为图谱实体项（chunk 项无此键）——单路级不造路名（A-16）。
 */

export const PATH_LABEL: Record<string, string> = {
  bm25: '稀疏',
  vector: '向量',
  graph: '图谱',
}

/** 融合轨迹中文串（["bm25","vector"] → "稀疏+向量"）；非融合级（空/null）→ null 不造。 */
export function formatPaths(paths?: string[] | null): string | null {
  if (!paths || paths.length === 0) return null
  return paths.map((p) => PATH_LABEL[p] ?? p).join('+')
}

/** 图谱实体项（kind="graph"）——chunk 项恒 false。 */
export function isGraphEntity(kind?: string | null): boolean {
  return kind === 'graph'
}
