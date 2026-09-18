package com.sonarcad.workflow.config;

import com.sonarcad.workflow.statemachine.PipelineEvent;
import com.sonarcad.workflow.statemachine.PipelineStatus;
import org.springframework.context.annotation.Configuration;
import org.springframework.statemachine.config.EnableStateMachineFactory;
import org.springframework.statemachine.config.StateMachineConfigurerAdapter;
import org.springframework.statemachine.config.builders.StateMachineStateConfigurer;
import org.springframework.statemachine.config.builders.StateMachineTransitionConfigurer;

import java.util.EnumSet;

/**
 * 编排状态机（D-05 / A-05）：只做"按序调 Agent 传 IR、审阅关暂停/裁决/局部回环、维护 IR 版本"，
 * 不写业务决策。转移集必须与 PipelineTransitions.TRANSITIONS 一致（PipelineStateMachineTest 互证）。
 */
@Configuration
@EnableStateMachineFactory
public class PipelineStateMachineConfig extends StateMachineConfigurerAdapter<PipelineStatus, PipelineEvent> {

    @Override
    public void configure(StateMachineStateConfigurer<PipelineStatus, PipelineEvent> states) throws Exception {
        states.withStates()
                .initial(PipelineStatus.pending)
                .states(EnumSet.allOf(PipelineStatus.class))
                .end(PipelineStatus.completed);
        // failed 不是终态：RETRY 可回到 running（阶段 5 验收反馈）
    }

    @Override
    public void configure(StateMachineTransitionConfigurer<PipelineStatus, PipelineEvent> transitions) throws Exception {
        transitions
                .withExternal().source(PipelineStatus.pending).target(PipelineStatus.running).event(PipelineEvent.START)
                .and()
                .withExternal().source(PipelineStatus.running).target(PipelineStatus.paused).event(PipelineEvent.PAUSE)
                .and()
                .withExternal().source(PipelineStatus.paused).target(PipelineStatus.running).event(PipelineEvent.RESUME)
                .and()
                .withExternal().source(PipelineStatus.running).target(PipelineStatus.completed).event(PipelineEvent.COMPLETE)
                .and()
                .withExternal().source(PipelineStatus.running).target(PipelineStatus.failed).event(PipelineEvent.FAIL)
                .and()
                .withExternal().source(PipelineStatus.failed).target(PipelineStatus.running).event(PipelineEvent.RETRY);
    }
}
