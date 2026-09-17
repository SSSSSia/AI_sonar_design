package com.sonarcad.workflow.statemachine;

/**
 * 会话流水线状态（D-05，PHASE-001 §3.1）：
 * pending → running → paused → completed / failed（completed/failed 为终态）。
 */
public enum PipelineStatus { pending, running, paused, completed, failed }
