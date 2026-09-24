/**
 * 站内导航拦截（配合新建会话页草稿二次确认）。
 * react-router 声明式路由（<Routes>）用不了 useBlocker（仅 data router 支持），
 * 因此在已知的跳转源头（AppLayout 菜单、页面返回按钮）调用 checkNavGuard：
 * 页面挂载时注册守卫，返回 false 表示有确认框待处理、本次跳转应取消。
 * 浏览器前进/后退无法拦截——兜底是草稿本就自动落 localStorage，回来可恢复。
 */

type NavGuard = (targetPath: string) => boolean

let guard: NavGuard | null = null

export function setNavGuard(g: NavGuard | null): void {
  guard = g
}

export function checkNavGuard(targetPath: string): boolean {
  return !guard || guard(targetPath)
}
