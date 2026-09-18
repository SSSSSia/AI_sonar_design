package com.sonarcad.workflow.statemachine;

/**
 * 编排事件。
 * 状态机事件：START/PAUSE/RESUME/COMPLETE/FAIL（审阅关 PAUSE 语义阶段 2 接入）；
 * RETRY（阶段 5 验收反馈接入）：失败会话按 current_stage 重新派发，failed → running。
 * STAGE_DONE：worker 单个 Agent 完成的内部回调——只续派下一棒、不改会话状态
 * （中间阶段完成 ≠ 会话完成；管线终点 A3 完成由 worker 直接发 COMPLETE 走状态机终止）。
 */
public enum PipelineEvent { START, PAUSE, RESUME, COMPLETE, FAIL, RETRY, STAGE_DONE }
