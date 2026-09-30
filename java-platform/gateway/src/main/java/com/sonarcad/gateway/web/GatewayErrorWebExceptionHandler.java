package com.sonarcad.gateway.web;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.reactive.error.ErrorWebExceptionHandler;
import org.springframework.core.annotation.Order;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

/**
 * T9.4（PHASE-009）网关级结构化错误体：限流 429（RequestRateLimiter 抛
 * ResponseStatusException）与下游 5xx 统一返回
 * {"code","message","data":null,"trace_id":"gateway"[,"retryAfterSeconds"]}
 * + Retry-After 响应头——对齐 JwtAuthGlobalFilter 的 401 手写体格式，
 * 前端 axios 拦截器据此做幂等 GET 指数退避。
 * order=-2：盖过默认 DefaultErrorWebExceptionHandler（-1）。
 */
@Component
@Order(-2)
public class GatewayErrorWebExceptionHandler implements ErrorWebExceptionHandler {

    private final int rateLimitRetryAfterSeconds;
    private final int cbRetryAfterSeconds;

    public GatewayErrorWebExceptionHandler(
            @Value("${gateway.fallback.rateLimitRetryAfterSeconds:1}") int rateLimitRetryAfterSeconds,
            @Value("${gateway.fallback.retryAfterSeconds:30}") int cbRetryAfterSeconds) {
        this.rateLimitRetryAfterSeconds = rateLimitRetryAfterSeconds;
        this.cbRetryAfterSeconds = cbRetryAfterSeconds;
    }

    @Override
    public Mono<Void> handle(ServerWebExchange exchange, Throwable ex) {
        if (exchange.getResponse().isCommitted()) {
            return Mono.error(ex);
        }
        HttpStatus status = resolveStatus(ex);
        String message = resolveMessage(ex, status);
        int retryAfter = switch (status) {
            case TOO_MANY_REQUESTS -> rateLimitRetryAfterSeconds;
            case SERVICE_UNAVAILABLE, BAD_GATEWAY, GATEWAY_TIMEOUT -> cbRetryAfterSeconds;
            default -> -1;
        };

        exchange.getResponse().setStatusCode(status);
        exchange.getResponse().getHeaders().setContentType(MediaType.APPLICATION_JSON);
        if (retryAfter >= 0) {
            exchange.getResponse().getHeaders().set("Retry-After", String.valueOf(retryAfter));
        }
        String body = "{\"code\":" + status.value()
                + ",\"message\":\"" + escape(message) + "\""
                + ",\"data\":null,\"trace_id\":\"gateway\""
                + (retryAfter >= 0 ? ",\"retryAfterSeconds\":" + retryAfter : "")
                + "}";
        byte[] bytes = body.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        DataBuffer buffer = exchange.getResponse().bufferFactory().wrap(bytes);
        return exchange.getResponse().writeWith(Mono.just(buffer));
    }

    private HttpStatus resolveStatus(Throwable ex) {
        if (ex instanceof ResponseStatusException rse && rse.getStatusCode() instanceof HttpStatus hs) {
            return hs;
        }
        return HttpStatus.INTERNAL_SERVER_ERROR;
    }

    private String resolveMessage(Throwable ex, HttpStatus status) {
        if (status == HttpStatus.TOO_MANY_REQUESTS) {
            return "请求过于频繁（限流保护）：已超出当前用户的令牌桶容量，请稍后重试——"
                    + "幂等查询会自动退避，请勿手工连续刷新";
        }
        if (ex instanceof ResponseStatusException rse && rse.getReason() != null) {
            return rse.getReason();
        }
        return "网关转发失败（" + status.value() + "），请稍后重试";
    }

    private String escape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
