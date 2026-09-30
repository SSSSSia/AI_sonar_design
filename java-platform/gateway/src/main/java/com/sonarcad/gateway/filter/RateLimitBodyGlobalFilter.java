package com.sonarcad.gateway.filter;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.http.server.reactive.ServerHttpResponseDecorator;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * T9.4（PHASE-009）限流 429 结构化降级：内置 RequestRateLimiter 拒绝时仅
 * setStatusCode(429) + setComplete()（空体，无 Retry-After），前端无法结构化退避。
 * 本过滤器在限流过滤器之前装饰响应，捕获 429 的 setComplete() 改写为结构化 JSON：
 * {"code":429,"message","data":null,"trace_id":"gateway","retryAfterSeconds"} + Retry-After 头。
 * order=-99：晚于 JwtAuthGlobalFilter(-100)、早于全部路由过滤器（限流在内）。
 */
@Component
public class RateLimitBodyGlobalFilter implements GlobalFilter, Ordered {

    private final int retryAfterSeconds;

    public RateLimitBodyGlobalFilter(
            @Value("${gateway.fallback.rateLimitRetryAfterSeconds:1}") int retryAfterSeconds) {
        this.retryAfterSeconds = retryAfterSeconds;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        ServerHttpResponseDecorator decorated = new ServerHttpResponseDecorator(exchange.getResponse()) {
            @Override
            public Mono<Void> setComplete() {
                if (getStatusCode() == HttpStatus.TOO_MANY_REQUESTS) {
                    getHeaders().setContentType(MediaType.APPLICATION_JSON);
                    getHeaders().set("Retry-After", String.valueOf(retryAfterSeconds));
                    String message = "请求过于频繁（限流保护）：已超出当前用户的令牌桶容量，请稍后重试——"
                            + "幂等查询会自动退避，请勿手工连续刷新";
                    byte[] body = ("{\"code\":429,\"message\":\"" + message + "\",\"data\":null"
                            + ",\"trace_id\":\"gateway\",\"retryAfterSeconds\":" + retryAfterSeconds + "}")
                            .getBytes(java.nio.charset.StandardCharsets.UTF_8);
                    DataBuffer buffer = bufferFactory().wrap(body);
                    return writeWith(Mono.just(buffer));
                }
                return super.setComplete();
            }
        };
        ServerWebExchange mutated = exchange.mutate().response((ServerHttpResponse) decorated).build();
        return chain.filter(mutated);
    }

    @Override
    public int getOrder() {
        return -99;
    }
}
