package com.sonarcad.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * API 网关（D-03）：统一入口，路由/负载均衡/限流/熔断。
 * 职责边界：只做流量转发与入口策略，不做业务决策（对应宪法 A-05 同源原则）。
 */
@SpringBootApplication
public class GatewayApplication {

    public static void main(String[] args) {
        SpringApplication.run(GatewayApplication.class, args);
    }
}
