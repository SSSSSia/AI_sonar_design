package com.sonarcad.gateway.config;

import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import reactor.core.publisher.Mono;

import java.net.InetSocketAddress;

/**
 * T9.4（PHASE-009）限流键解析：JWT 用户优先（JwtAuthGlobalFilter order=-100 先注入
 * X-User-Id 信任头，本解析器执行时可见），无用户（登录中）回退客户端 IP。
 */
@Configuration
public class RateLimitConfig {

    @Bean
    public KeyResolver userKeyResolver() {
        return exchange -> {
            String uid = exchange.getRequest().getHeaders().getFirst("X-User-Id");
            if (uid == null || uid.isBlank()) {
                InetSocketAddress remote = exchange.getRequest().getRemoteAddress();
                uid = remote != null ? remote.getAddress().getHostAddress() : "unknown";
            }
            return Mono.just(uid);
        };
    }
}
