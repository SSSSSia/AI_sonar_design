package com.sonarcad.workflow.statemachine;

/** 状态机事件：START/PAUSE/RESUME/COMPLETE/FAIL。审阅关触发的 PAUSE 语义在阶段 2（T2.2）接入。 */
public enum PipelineEvent { START, PAUSE, RESUME, COMPLETE, FAIL }
