package com.sonarcad.workflow;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 编排状态机（D-05）。职责边界（宪法 A-05，只做三件事）：
 * 1. 按序调用 Agent 传递 IR（A-01 固定管线，禁止 LLM 决定下一步）；
 * 2. 审阅关暂停/裁决/局部回环（A-11）；
 * 3. 维护 IR 版本（A-12 不可变快照）。
 * 不做业务决策、不写业务逻辑。
 */
@SpringBootApplication
@MapperScan({"com.sonarcad.workflow.session", "com.sonarcad.workflow.audit"})
public class WorkflowApplication {

    public static void main(String[] args) {
        SpringApplication.run(WorkflowApplication.class, args);
    }
}
