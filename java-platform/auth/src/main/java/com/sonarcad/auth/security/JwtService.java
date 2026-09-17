package com.sonarcad.auth.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Date;
import java.util.List;

/**
 * JWT 签发与解析（HS256）。
 * 密钥来自 .env 的 JWT_SECRET（宪法 §10：密钥不入库不入码），网关与本服务共享。
 * claims 约定：sub=username，roles=角色 code 列表（网关校验后以 X-User-* 头转发给下游）。
 */
@Service
public class JwtService {

    private final SecretKey key;
    private final Duration ttl;

    public JwtService(@Value("${jwt.secret}") String secret,
                      @Value("${jwt.ttl-hours:12}") long ttlHours) {
        this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
        this.ttl = Duration.ofHours(ttlHours);
    }

    public String generate(String userId, String username, List<String> roles) {
        Date now = new Date();
        return Jwts.builder()
                .subject(userId)                    // sub = 用户 UUID（下游 owner_id/created_by 外键）
                .claim("username", username)
                .claim("roles", roles)
                .issuedAt(now)
                .expiration(new Date(now.getTime() + ttl.toMillis()))
                .signWith(key)
                .compact();
    }

    public record ParsedUser(String userId, String username, List<String> roles) {}

    public ParsedUser parse(String token) {
        Claims claims = Jwts.parser().verifyWith(key).build()
                .parseSignedClaims(token).getPayload();
        Object roles = claims.get("roles");
        List<String> roleList = roles instanceof List<?> list
                ? list.stream().map(String::valueOf).toList()
                : List.of();
        return new ParsedUser(claims.getSubject(),
                String.valueOf(claims.getOrDefault("username", claims.getSubject())), roleList);
    }
}
