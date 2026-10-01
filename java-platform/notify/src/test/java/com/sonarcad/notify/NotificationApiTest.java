package com.sonarcad.notify;

import com.sonarcad.notify.store.NotificationEntity;
import com.sonarcad.notify.store.NotificationMapper;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * T11.1 通知持久化 API 测试：扇出落库接线、查询/已读/清理端点、降级语义。
 * mapper 全 mock（无需真实 PG）；WS 链路本体回归在 NotifyIntegrationTest。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class NotificationApiTest {

    @LocalServerPort
    int port;

    @Autowired
    TestRestTemplate rest;

    @MockBean
    NotificationMapper mapper;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("jwt.secret", () -> "test-secret-notify");
    }

    private HttpHeaders trustHeaders() {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-User-Id", "user-1");
        return headers;
    }

    // ---- 落库接线 ----

    @Test
    void publishPersistsReviewRequestToReviewerRoles() {
        HttpHeaders headers = trustHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        rest.exchange("http://localhost:" + port + "/internal/sessions/sess-9/messages",
                HttpMethod.POST,
                new HttpEntity<>(Map.of("type", "review_request",
                        "payload", Map.of("gate", "gate1", "ir_type", "IR2", "ir_version", 1, "summary", "拖曳阵")), headers),
                String.class);
        ArgumentCaptor<String> type = ArgumentCaptor.forClass(String.class);
        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<String>> roles = ArgumentCaptor.forClass(List.class);
        verify(mapper).fanOutInsert(eq("sess-9"), anyString(), type.capture(), isNull(), anyString(), roles.capture());
        assertThat(type.getValue()).isEqualTo("review_request");
        assertThat(roles.getValue()).containsExactly("reviewer", "admin");
    }

    @Test
    void alertPersistsToAllUsersWithTitle() {
        HttpHeaders headers = trustHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        rest.exchange("http://localhost:" + port + "/internal/alerts", HttpMethod.POST,
                new HttpEntity<>(Map.of("status", "firing", "alerts",
                        List.of(Map.of("labels", Map.of("alertname", "X", "severity", "warning")))), headers),
                String.class);
        ArgumentCaptor<String> title = ArgumentCaptor.forClass(String.class);
        verify(mapper).fanOutInsert(anyString(), anyString(), eq("alert"), title.capture(), anyString(), isNull());
        assertThat(title.getValue()).contains("系统告警").contains("firing");
    }

    @Test
    void processTypesAreNotPersisted() {
        HttpHeaders headers = trustHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        rest.exchange("http://localhost:" + port + "/internal/sessions/sess-9/messages",
                HttpMethod.POST,
                new HttpEntity<>(Map.of("type", "task_progress",
                        "payload", Map.of("stage", "A1", "status", "running", "progress", 10)), headers),
                String.class);
        verify(mapper, never()).fanOutInsert(anyString(), anyString(), anyString(), any(), anyString(), any());
    }

    @Test
    void publishStillBroadcastsWhenPersistenceFails() {
        when(mapper.fanOutInsert(anyString(), anyString(), anyString(), any(), anyString(), any()))
                .thenThrow(new RuntimeException("db down"));
        HttpHeaders headers = trustHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        ResponseEntity<String> resp = rest.exchange(
                "http://localhost:" + port + "/internal/sessions/sess-9/messages",
                HttpMethod.POST,
                new HttpEntity<>(Map.of("type", "review_request",
                        "payload", Map.of("gate", "gate2", "ir_type", "IR4", "ir_version", 1)), headers),
                String.class);
        // 推送是增强而非权威：落库失败不影响广播受理
        assertThat(resp.getStatusCode().value()).isEqualTo(200);
        assertThat(resp.getBody()).contains("\"accepted\":true");
    }

    // ---- 查询/已读/清理 ----

    @Test
    void listRequiresTrustHeader() {
        ResponseEntity<String> resp = rest.getForEntity(
                "http://localhost:" + port + "/api/v1/notifications", String.class);
        assertThat(resp.getStatusCode().value()).isEqualTo(401);
    }

    @Test
    void listReturnsItemsWithUnreadCount() {
        NotificationEntity row = new NotificationEntity();
        row.setId("n-1");
        row.setClientKey("k-1");
        row.setSessionId("sess-9");
        row.setType("review_request");
        row.setTitle(null);
        row.setPayload("{\"gate\":\"gate1\",\"summary\":\"拖曳阵\"}");
        row.setReadAt(null);
        row.setCreatedAt(OffsetDateTime.now());
        when(mapper.listForUser(eq("user-1"), anyInt(), anyInt())).thenReturn(List.of(row));
        when(mapper.countForUser("user-1")).thenReturn(7L);
        when(mapper.countUnread("user-1")).thenReturn(2L);

        ResponseEntity<Map> resp = rest.exchange(
                "http://localhost:" + port + "/api/v1/notifications?limit=10",
                HttpMethod.GET, new HttpEntity<>(trustHeaders()), Map.class);
        assertThat(resp.getStatusCode().value()).isEqualTo(200);
        // 平台统一响应包（宪法 §5.3）：{code:0, message, data}
        assertThat(resp.getBody().get("code")).isEqualTo(0);
        @SuppressWarnings("unchecked")
        Map<String, Object> body = (Map<String, Object>) resp.getBody().get("data");
        assertThat(body.get("total")).isEqualTo(7);
        assertThat(body.get("unread")).isEqualTo(2);
        List<Map<String, Object>> items = (List<Map<String, Object>>) body.get("items");
        assertThat(items).hasSize(1);
        assertThat(items.get(0)).containsEntry("id", "n-1").containsEntry("read", false);
        assertThat(items.get(0)).containsEntry("client_key", "k-1");
        assertThat(items.get(0).get("payload")).isInstanceOf(Map.class);
    }

    @Test
    void queryReturns503WhenStorageUnavailable() {
        when(mapper.listForUser(anyString(), anyInt(), anyInt())).thenThrow(new RuntimeException("db down"));
        ResponseEntity<String> resp = rest.exchange(
                "http://localhost:" + port + "/api/v1/notifications",
                HttpMethod.GET, new HttpEntity<>(trustHeaders()), String.class);
        assertThat(resp.getStatusCode().value()).isEqualTo(503);
    }

    @Test
    void markReadAndReadAllAndClearScopes() {
        when(mapper.markRead("n-1", "user-1")).thenReturn(1);
        when(mapper.markAllRead("user-1")).thenReturn(5);
        when(mapper.clearRead("user-1")).thenReturn(3);
        when(mapper.clearAll("user-1")).thenReturn(9);

        assertThat(rest.exchange("http://localhost:" + port + "/api/v1/notifications/n-1/read",
                HttpMethod.POST, new HttpEntity<>(trustHeaders()), String.class).getStatusCode().value()).isEqualTo(200);
        verify(mapper).markRead("n-1", "user-1");

        assertThat(rest.exchange("http://localhost:" + port + "/api/v1/notifications/read-all",
                HttpMethod.POST, new HttpEntity<>(trustHeaders()), String.class).getStatusCode().value()).isEqualTo(200);
        verify(mapper).markAllRead("user-1");

        assertThat(rest.exchange("http://localhost:" + port + "/api/v1/notifications?scope=read",
                HttpMethod.DELETE, new HttpEntity<>(trustHeaders()), String.class).getStatusCode().value()).isEqualTo(200);
        verify(mapper).clearRead("user-1");

        assertThat(rest.exchange("http://localhost:" + port + "/api/v1/notifications?scope=all",
                HttpMethod.DELETE, new HttpEntity<>(trustHeaders()), String.class).getStatusCode().value()).isEqualTo(200);
        verify(mapper).clearAll("user-1");

        // 非法 scope 400；越权删除按用户隔离
        assertThat(rest.exchange("http://localhost:" + port + "/api/v1/notifications?scope=bogus",
                HttpMethod.DELETE, new HttpEntity<>(trustHeaders()), String.class).getStatusCode().value()).isEqualTo(400);
        assertThat(rest.exchange("http://localhost:" + port + "/api/v1/notifications/n-1",
                HttpMethod.DELETE, new HttpEntity<>(trustHeaders()), String.class).getStatusCode().value()).isEqualTo(200);
        verify(mapper).deleteForUser("n-1", "user-1");
    }
}
