package com.sonarcad.workflow.statemachine;

import java.util.Map;
import java.util.Set;

/**
 * 合法转移表（单一事实源，与 PipelineStateMachineConfig 的状态机定义由单测互证）。
 * 会话中断恢复时按 DB 中的 pipeline_status 重建机器（A-12 断点恢复的入口）。
 */
public final class PipelineTransitions {

    private PipelineTransitions() {}

    public static final Map<PipelineStatus, Map<PipelineEvent, PipelineStatus>> TRANSITIONS = Map.of(
            PipelineStatus.pending, Map.of(
                    PipelineEvent.START, PipelineStatus.running),
            PipelineStatus.running, Map.of(
                    PipelineEvent.PAUSE, PipelineStatus.paused,
                    PipelineEvent.COMPLETE, PipelineStatus.completed,
                    PipelineEvent.FAIL, PipelineStatus.failed),
            PipelineStatus.paused, Map.of(
                    PipelineEvent.RESUME, PipelineStatus.running),
            PipelineStatus.completed, Map.of(),
            PipelineStatus.failed, Map.of()
    );

    /** 返回目标状态；非法转移返回 null（调用方回 409） */
    public static PipelineStatus targetOf(PipelineStatus current, PipelineEvent event) {
        return TRANSITIONS.getOrDefault(current, Map.of()).get(event);
    }

    /** 当前状态下可触发的事件（供 API 自省/前端禁用按钮） */
    public static Set<PipelineEvent> allowedEvents(PipelineStatus current) {
        return TRANSITIONS.getOrDefault(current, Map.of()).keySet();
    }
}
