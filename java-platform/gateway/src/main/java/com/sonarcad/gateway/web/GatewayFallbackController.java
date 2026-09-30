package com.sonarcad.gateway.web;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * T9.4（PHASE-009）熔断降级端点：pyEngine 熔断打开时 CircuitBreaker 过滤器
 * forward 到此，返回结构化 503（对齐 401 手写体格式 + retryAfterSeconds + Retry-After 头）。
 */
@RestController
@RequestMapping("/fallback")
public class GatewayFallbackController {

    private final int retryAfterSeconds;

    public GatewayFallbackController(@Value("${gateway.fallback.retryAfterSeconds:30}") int retryAfterSeconds) {
        this.retryAfterSeconds = retryAfterSeconds;
    }

    /** 不限方法：CircuitBreaker forward 保留原始请求方法（GET/POST 均可能） */
    @RequestMapping("/py-engine")
    public Mono<Void> pyEngine(ServerWebExchange exchange) {
        String message = "py-engine 算力服务熔断保护中（连续失败触发），请稍后重试——幂等查询会自动退避，"
                + "审阅/裁决等写操作请勿重复提交";
        exchange.getResponse().setStatusCode(HttpStatus.SERVICE_UNAVAILABLE);
        exchange.getResponse().getHeaders().setContentType(MediaType.APPLICATION_JSON);
        exchange.getResponse().getHeaders().set("Retry-After", String.valueOf(retryAfterSeconds));
        byte[] body = ("{\"code\":503,\"message\":\"" + message + "\",\"data\":null,\"trace_id\":\"gateway\""
                + ",\"retryAfterSeconds\":" + retryAfterSeconds + "}")
                .getBytes(java.nio.charset.StandardCharsets.UTF_8);
        return exchange.getResponse().writeWith(
                Mono.just(exchange.getResponse().bufferFactory().wrap(body)));
    }
}
