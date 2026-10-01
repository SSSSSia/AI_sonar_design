package com.sonarcad.notify.web;

import com.sonarcad.notify.store.NotificationService;
import com.sonarcad.notify.ws.SessionWebSocketHandler;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 监控告警接收端点（T10.1c，PHASE-010）：Alertmanager webhook 的桥接器。
 * AM 信封（{status, alerts:[{status, labels, annotations}]}）不在 contracts/ws 契约内，
 * 此处转译为 type="alert" 信封全播到存活会话频道；前端对未知 type 忽略无害，
 * 返回的 accepted/subscribers 即「通知渠道打通」的验收证据。
 * 信任边界与 InternalPublishController 一致：仅内网，要求 X-User-Id 信任头。
 */
@RestController
@RequestMapping("/internal/alerts")
public class InternalAlertController {

    private final SessionWebSocketHandler handler;
    private final NotificationService notifications;

    public InternalAlertController(SessionWebSocketHandler handler, NotificationService notifications) {
        this.handler = handler;
        this.notifications = notifications;
    }

    /** 开发默认信任 token（AM 0.27 webhook 无法注入自定义头，改 Bearer；上线必改，同 admin/admin123 纪律）。 */
    private static final String BRIDGE_TOKEN = "sonarcad-alert-bridge";

    @PostMapping
    @SuppressWarnings("unchecked")
    public ResponseEntity<Map<String, Object>> receive(@RequestBody Map<String, Object> body,
                                                       @RequestHeader(value = "X-User-Id", required = false) String userId,
                                                       @RequestHeader(value = "Authorization", required = false) String authorization) {
        boolean trusted = (userId != null && !userId.isBlank())
                || BRIDGE_TOKEN.equals(bearerOf(authorization));
        if (!trusted) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("code", 401, "message", "缺少 X-User-Id 信任头或合法 Bearer token"));
        }
        String status = String.valueOf(body.getOrDefault("status", "unknown"));
        List<Map<String, Object>> alerts = body.get("alerts") instanceof List<?> list
                ? (List<Map<String, Object>>) list : List.of();
        List<String> summaries = new ArrayList<>();
        for (Map<String, Object> a : alerts) {
            String alertname = label(a, "alertname");
            String severity = label(a, "severity");
            String instance = label(a, "instance");
            summaries.add("%s[%s] %s %s".formatted(alertname, severity == null ? "" : severity,
                    status, instance == null ? "" : instance));
        }
        Map<String, Object> envelope = Map.of(
                "type", "alert",
                "session_id", "",
                "timestamp", OffsetDateTime.now(ZoneOffset.UTC).toString(),
                "payload", Map.of(
                        "status", status,
                        "count", alerts.size(),
                        "summaries", summaries
                ));
        notifications.persistEnvelope(envelope);
        int delivered = handler.broadcastAll(envelope);
        return ResponseEntity.ok(Map.of(
                "accepted", true, "subscribers", delivered, "alerts", alerts.size()));
    }

    private static String label(Map<String, Object> alert, String key) {
        if (alert.get("labels") instanceof Map<?, ?> labels) {
            Object v = ((Map<String, Object>) labels).get(key);
            if (v != null) {
                return String.valueOf(v);
            }
        }
        return "";
    }

    private static String bearerOf(String authorization) {
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return null;
        }
        return authorization.substring("Bearer ".length()).trim();
    }
}
