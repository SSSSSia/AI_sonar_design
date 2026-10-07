package com.sonarcad.auth;

import com.sonarcad.auth.audit.AuditMapper;
import com.sonarcad.auth.audit.AuditRecorder;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * T13.1 审计旁路记录器单测：字段透传、detail 序列化为 JSON 文本、
 * Mapper 抛异常时静默（旁路不阻断主流程——§9.4 审计是旁路语义）。
 * SQL 正确性由容器栈实机验证承接（与 UserControllerTest 同口径）。
 */
class AuditRecorderTest {

    private final AuditMapper mapper = mock(AuditMapper.class);
    private final AuditRecorder recorder = new AuditRecorder(mapper);

    @Test
    void recordPassesFieldsAndSerializesDetail() {
        recorder.record("u-1", "alice", "auth.login", "user", "u-1",
                Map.of("result", "success"), "10.0.0.1");

        verify(mapper).insert(eq("u-1"), eq("alice"), eq("auth.login"), eq("user"), eq("u-1"),
                eq("{\"result\":\"success\"}"), eq("10.0.0.1"));
    }

    @Test
    void recordWithNullDetailAndNullIp() {
        recorder.record(null, "bob", "auth.login", "user", null, null, null);

        verify(mapper).insert(isNull(), eq("bob"), eq("auth.login"), eq("user"), isNull(),
                isNull(), isNull());
    }

    @Test
    void mapperFailureIsSwallowed() {
        when(mapper.insert(any(), any(), any(), any(), any(), any(), any()))
                .thenThrow(new RuntimeException("db down"));

        assertThatCode(() -> recorder.record("u-1", "alice", "user.create", "user", "u-1",
                Map.of("target", "x"), null)).doesNotThrowAnyException();
    }
}
