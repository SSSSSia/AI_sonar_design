package com.sonarcad.gateway.filter;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;

/**
 * 网关 JWT 全局过滤器（PHASE-001 §3.2 / D-03）：
 * 1. 白名单（/api/v1/auth/login）直接放行；
 * 2. 校验 Bearer Token（HS256，与 auth 共享 JWT_SECRET）；
 * 3. 通过后剥离外部伪造的 X-User-* 头，注入 X-User-Id / X-User-Roles 给下游（下游信任网关）。
 */
@Component
public class JwtAuthGlobalFilter implements GlobalFilter, Ordered {

    private static final String WHITELIST = "/api/v1/auth/login";

    private final SecretKey key;

    public JwtAuthGlobalFilter(@Value("${jwt.secret}") String secret) {
        this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String path = exchange.getRequest().getPath().value();
        if (path.equals(WHITELIST)) {
            return chain.filter(sanitize(exchange));
        }
        String auth = exchange.getRequest().getHeaders().getFirst("Authorization");
        if (auth == null || !auth.startsWith("Bearer ")) {
            return unauthorized(exchange, "缺少 Bearer Token");
        }
        try {
            Claims claims = Jwts.parser().verifyWith(key).build()
                    .parseSignedClaims(auth.substring(7)).getPayload();
            Object roles = claims.get("roles");
            String rolesHeader = roles instanceof java.util.List<?> list
                    ? String.join(",", list.stream().map(String::valueOf).toList()) : "";
            ServerWebExchange mutated = sanitize(exchange).mutate()
                    .request(b -> b.headers(h -> {
                        h.set("X-User-Id", claims.getSubject());
                        h.set("X-User-Roles", rolesHeader);
                    })).build();
            return chain.filter(mutated);
        } catch (Exception e) {
            return unauthorized(exchange, "Token 无效或已过期");
        }
    }

    /** 剥离客户端自带的信任头，防伪造 */
    private ServerWebExchange sanitize(ServerWebExchange exchange) {
        return exchange.mutate().request(b -> b.headers(h -> {
            h.remove("X-User-Id");
            h.remove("X-User-Roles");
        })).build();
    }

    private Mono<Void> unauthorized(ServerWebExchange exchange, String message) {
        exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
        exchange.getResponse().getHeaders().setContentType(MediaType.APPLICATION_JSON);
        byte[] body = ("{\"code\":401,\"message\":\"" + message + "\",\"data\":null,\"trace_id\":\"gateway\"}")
                .getBytes(StandardCharsets.UTF_8);
        return exchange.getResponse().writeWith(
                Mono.just(exchange.getResponse().bufferFactory().wrap(body)));
    }

    @Override
    public int getOrder() {
        return -100;
    }
}
