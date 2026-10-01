package com.sonarcad.notify.web;

import com.sonarcad.notify.store.NotificationEntity;
import com.sonarcad.notify.store.NotificationService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 通知查询/已读 API（T11.1，P2-4）：登录后拉取 + 已读/删除/清理，经 gateway /api/v1/notifications/** 路由。
 * 鉴权：gateway JWT 过滤器验签后注入 X-User-Id（直连内网调用也要求带）；
 * 所有读写均按该用户隔离（扇出行模型，见 V14）。
 */
@RestController
@RequestMapping("/api/v1/notifications")
public class NotificationController {

    private final NotificationService service;

    public NotificationController(NotificationService service) {
        this.service = service;
    }

    @GetMapping
    public ResponseEntity<Map<String, Object>> list(
            @RequestHeader(value = "X-User-Id", required = false) String userId,
            @RequestParam(defaultValue = "50") int limit,
            @RequestParam(defaultValue = "0") int offset) {
        if (userId == null || userId.isBlank()) {
            return unauthorized();
        }
        try {
            int safeLimit = Math.max(1, Math.min(limit, 200));
            int safeOffset = Math.max(0, offset);
            List<Map<String, Object>> items = new ArrayList<>();
            for (NotificationEntity n : service.list(userId, safeLimit, safeOffset)) {
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("id", n.getId());
                item.put("client_key", n.getClientKey());
                item.put("session_id", n.getSessionId());
                item.put("type", n.getType());
                item.put("title", n.getTitle());
                item.put("payload", parseJson(n.getPayload()));
                item.put("read", n.getReadAt() != null);
                item.put("created_at", n.getCreatedAt() == null ? null : n.getCreatedAt().toString());
                items.add(item);
            }
            return ResponseEntity.ok(ok(Map.of(
                    "items", items,
                    "total", service.total(userId),
                    "unread", service.unread(userId))));
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("code", 503, "message", "通知存储不可用：" + e.getMessage()));
        }
    }

    @PostMapping("/{id}/read")
    public ResponseEntity<Map<String, Object>> markRead(@PathVariable String id,
                                                        @RequestHeader(value = "X-User-Id", required = false) String userId) {
        if (userId == null || userId.isBlank()) {
            return unauthorized();
        }
        return ok(() -> Map.of("updated", service.markRead(id, userId)));
    }

    @PostMapping("/read-all")
    public ResponseEntity<Map<String, Object>> markAllRead(
            @RequestHeader(value = "X-User-Id", required = false) String userId) {
        if (userId == null || userId.isBlank()) {
            return unauthorized();
        }
        return ok(() -> Map.of("updated", service.markAllRead(userId)));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, Object>> remove(@PathVariable String id,
                                                      @RequestHeader(value = "X-User-Id", required = false) String userId) {
        if (userId == null || userId.isBlank()) {
            return unauthorized();
        }
        return ok(() -> Map.of("deleted", service.delete(id, userId)));
    }

    /** scope=read 清除已读；scope=all 清空（与前端通知中心「清除已读/清空」对齐，2026-09-25 用户反馈管理能力）。 */
    @DeleteMapping
    public ResponseEntity<Map<String, Object>> clear(
            @RequestParam String scope,
            @RequestHeader(value = "X-User-Id", required = false) String userId) {
        if (userId == null || userId.isBlank()) {
            return unauthorized();
        }
        if ("read".equals(scope)) {
            return ok(() -> Map.of("deleted", service.clearRead(userId)));
        }
        if ("all".equals(scope)) {
            return ok(() -> Map.of("deleted", service.clearAll(userId)));
        }
        return ResponseEntity.badRequest().body(Map.of("code", 400, "message", "scope 仅支持 read|all"));
    }

    /** 平台统一响应包（宪法 §5.3）：{code:0, message, data}——前端 axios 拦截器按此解包。 */
    private static Map<String, Object> ok(Map<String, Object> data) {
        return Map.of("code", 0, "message", "ok", "data", data);
    }

    private ResponseEntity<Map<String, Object>> ok(java.util.function.Supplier<Map<String, Object>> action) {
        try {
            return ResponseEntity.ok(ok(action.get()));
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body(Map.of("code", 503, "message", "通知存储不可用：" + e.getMessage()));
        }
    }

    private ResponseEntity<Map<String, Object>> unauthorized() {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .body(Map.of("code", 401, "message", "缺少 X-User-Id（应经 gateway 注入）"));
    }

    private Object parseJson(String payload) {
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper().readValue(
                    payload == null ? "{}" : payload, Object.class);
        } catch (Exception e) {
            return Map.of();
        }
    }
}
