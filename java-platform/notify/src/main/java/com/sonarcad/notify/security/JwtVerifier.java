package com.sonarcad.notify.security;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;

/**
 * 最小 HS512 JWT 验签（与 gateway 共享 JWT_SECRET）。
 * 只做签名 + exp 校验，不做角色解析——WS 频道是只读进度流，粒度到"登录用户"即可。
 * 不引入 jjwt 依赖：本服务只消费一种算法的 token，手写 HMAC 足够且面更小。
 */
@Component
public class JwtVerifier {

    private static final String HMAC_SHA512 = "HmacSHA512";
    private final byte[] secret;
    private final ObjectMapper mapper = new ObjectMapper();

    public JwtVerifier(@Value("${jwt.secret}") String secret) {
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
    }

    /** 校验失败抛 IllegalArgumentException（含过期/格式/签名）。返回 sub（用户 id）。 */
    public String verify(String token) {
        if (token == null || token.isBlank()) {
            throw new IllegalArgumentException("缺少 token");
        }
        String[] parts = token.split("\\.");
        if (parts.length != 3) {
            throw new IllegalArgumentException("token 格式非法");
        }
        byte[] expected = hmac((parts[0] + "." + parts[1]).getBytes(StandardCharsets.US_ASCII));
        byte[] actual;
        try {
            actual = Base64.getUrlDecoder().decode(parts[2]);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("token 签名段非 base64url");
        }
        if (!MessageDigestEqual.isEqual(expected, actual)) {
            throw new IllegalArgumentException("token 签名不匹配");
        }
        JsonNode claims;
        try {
            claims = mapper.readTree(Base64.getUrlDecoder().decode(parts[1]));
        } catch (Exception e) {
            throw new IllegalArgumentException("token claims 解析失败");
        }
        if (claims.has("exp") && Instant.now().getEpochSecond() >= claims.get("exp").asLong()) {
            throw new IllegalArgumentException("token 已过期");
        }
        return claims.has("sub") ? claims.get("sub").asText() : "";
    }

    private byte[] hmac(byte[] data) {
        try {
            Mac mac = Mac.getInstance(HMAC_SHA512);
            mac.init(new SecretKeySpec(secret, HMAC_SHA512));
            return mac.doFinal(data);
        } catch (Exception e) {
            throw new IllegalStateException("HMAC 初始化失败", e);
        }
    }

    /** 常量时间比较，避免逐字节短路。 */
    private static final class MessageDigestEqual {
        static boolean isEqual(byte[] a, byte[] b) {
            return java.security.MessageDigest.isEqual(a, b);
        }
    }
}
