package com.sonarcad.workflow;

import org.junit.jupiter.api.Test;
import org.springframework.security.access.prepost.PreAuthorize;

import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * T11.2 硬规则①（P2-5）：审阅关裁决权 = admin/reviewer。
 * 以注解反射守护 @PreAuthorize 不被回归移除（方法级安全的完整启用链路由
 * TrustedHeaderAuthFilter + @EnableMethodSecurity 在真实请求路径上承担，
 * Gateway /api/v1/workflow/** 路由的 JWT→信任头注入为前置）。
 */
class ReviewControllerRbacTest {

    private static final Map<String, String> EXPECTED = new LinkedHashMap<>();

    static {
        EXPECTED.put("decide", "hasAnyRole('admin','reviewer')");
    }

    @Test
    void gateDecideIsRestrictedToAdminAndReviewer() throws Exception {
        Class<?> controller = Class.forName("com.sonarcad.workflow.web.ReviewController");
        for (Map.Entry<String, String> e : EXPECTED.entrySet()) {
            Method m = findMethod(controller, e.getKey());
            PreAuthorize pa = m.getAnnotation(PreAuthorize.class);
            assertThat(pa).as("ReviewController.%s 必须带 @PreAuthorize（T11.2 硬规则①）", e.getKey()).isNotNull();
            assertThat(pa.value()).isEqualTo(e.getValue());
        }
    }

    private static Method findMethod(Class<?> type, String name) {
        for (Method m : type.getDeclaredMethods()) {
            if (m.getName().equals(name)) {
                return m;
            }
        }
        throw new IllegalStateException("method not found: " + name);
    }
}
