package com.sonarcad.auth;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * 认证鉴权服务（D-04）：Spring Security + JWT + RBAC。
 * 职责边界：颁发/校验令牌、角色与权限矩阵管理；不参与工作流编排。
 */
@SpringBootApplication
@MapperScan({"com.sonarcad.auth.user", "com.sonarcad.auth.audit"})
public class AuthApplication {

    public static void main(String[] args) {
        SpringApplication.run(AuthApplication.class, args);
    }
}
