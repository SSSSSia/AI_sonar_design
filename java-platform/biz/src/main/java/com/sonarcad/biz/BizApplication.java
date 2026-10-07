package com.sonarcad.biz;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 业务管理服务：项目管理(5.7)/会话状态机(5.8)/版本管理(5.9)/任务管理(5.10)/
 * 用户权限(5.11)/交付物管理(5.12)。
 * 职责边界：业务数据 CRUD 与状态落地；不直接调用 Agent、不做算力逻辑。
 */
@SpringBootApplication
@MapperScan({"com.sonarcad.biz.project", "com.sonarcad.biz.member", "com.sonarcad.biz.audit"})
public class BizApplication {

    public static void main(String[] args) {
        SpringApplication.run(BizApplication.class, args);
    }
}
