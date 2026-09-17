package com.sonarcad.workflow;

import com.sonarcad.workflow.config.PipelineStateMachineConfig;
import com.sonarcad.workflow.statemachine.PipelineEvent;
import com.sonarcad.workflow.statemachine.PipelineStatus;
import com.sonarcad.workflow.statemachine.PipelineTransitions;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.statemachine.StateMachine;
import org.springframework.statemachine.config.StateMachineFactory;

import static org.junit.jupiter.api.Assertions.*;

/**
 * 契约互证（PHASE-001 §3.1）：Spring StateMachine 实际可走的转移必须与
 * PipelineTransitions.TRANSITIONS（落库裁决用）完全一致，防两处定义漂移。
 */
@SpringBootTest(classes = PipelineStateMachineConfig.class)
class PipelineStateMachineTest {

    @Autowired
    private StateMachineFactory<PipelineStatus, PipelineEvent> factory;

    @Test
    void machineTransitionsMatchTransitionTable() {
        for (PipelineStatus from : PipelineStatus.values()) {
            for (PipelineEvent event : PipelineEvent.values()) {
                StateMachine<PipelineStatus, PipelineEvent> m = freshMachineAt(from);
                boolean accepted = m.sendEvent(event);
                boolean tableLegal = PipelineTransitions.targetOf(from, event) != null;
                assertEquals(tableLegal, accepted,
                        () -> from + " --" + event + "--> 机器=" + accepted + " 转移表=" + tableLegal);
                if (accepted) {
                    assertEquals(PipelineTransitions.targetOf(from, event), m.getState().getId(),
                            () -> from + " --" + event + "--> 目标状态不一致");
                }
            }
        }
    }

    @Test
    void terminalStatesHaveNoOutgoingTransitions() {
        assertTrue(PipelineTransitions.allowedEvents(PipelineStatus.completed).isEmpty());
        assertTrue(PipelineTransitions.allowedEvents(PipelineStatus.failed).isEmpty());
    }

    /** 从 pending 合法推进到目标状态后返回机器 */
    private StateMachine<PipelineStatus, PipelineEvent> freshMachineAt(PipelineStatus target) {
        StateMachine<PipelineStatus, PipelineEvent> m = factory.getStateMachine();
        m.start();
        assertEquals(PipelineStatus.pending, m.getState().getId());
        switch (target) {
            case pending -> { }
            case running -> assertTrue(m.sendEvent(PipelineEvent.START));
            case paused -> {
                assertTrue(m.sendEvent(PipelineEvent.START));
                assertTrue(m.sendEvent(PipelineEvent.PAUSE));
            }
            case completed -> {
                assertTrue(m.sendEvent(PipelineEvent.START));
                assertTrue(m.sendEvent(PipelineEvent.COMPLETE));
            }
            case failed -> {
                assertTrue(m.sendEvent(PipelineEvent.START));
                assertTrue(m.sendEvent(PipelineEvent.FAIL));
            }
        }
        assertEquals(target, m.getState().getId(), "驱动到目标状态失败");
        return m;
    }
}
