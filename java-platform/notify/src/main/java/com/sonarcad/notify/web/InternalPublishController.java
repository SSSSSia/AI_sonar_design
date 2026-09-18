package com.sonarcad.notify.web;

import com.sonarcad.notify.ws.SessionWebSocketHandler;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Map;
import java.util.Set;

/**
 * 内部发布端点：workflow / py-engine 把符合 contracts/ws schema 的信封推到会话频道。
 * 信任边界：仅内网服务可调（要求 X-User-Id 信任头，与平台其他内部调用一致）；无独立鉴权体系。
 */
@RestController
@RequestMapping("/internal/sessions/{sid}/messages")
public class InternalPublishController {

    private static final Set<String> ALLOWED_TYPES = Set.of("task_progress", "log", "review_request", "ir_updated");

    private final SessionWebSocketHandler handler;

    public InternalPublishController(SessionWebSocketHandler handler) {
        this.handler = handler;
    }

    @PostMapping
    public ResponseEntity<Map<String, Object>> publish(@PathVariable String sid,
                                                       @RequestBody Map<String, Object> envelope,
                                                       @RequestHeader(value = "X-User-Id", required = false) String userId) {
        if (userId == null || userId.isBlank()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("code", 401, "message", "缺少 X-User-Id 信任头"));
        }
        Object type = envelope.get("type");
        if (type == null || !ALLOWED_TYPES.contains(type.toString())) {
            return ResponseEntity.badRequest()
                    .body(Map.of("code", 400, "message", "非法消息 type: " + type + "（契约见 contracts/ws）"));
        }
        if (envelope.get("payload") == null) {
            return ResponseEntity.badRequest().body(Map.of("code", 400, "message", "缺少 payload"));
        }
        envelope.put("session_id", sid);
        envelope.putIfAbsent("timestamp", OffsetDateTime.now(ZoneOffset.UTC).toString());
        int delivered = handler.broadcast(sid, envelope);
        return ResponseEntity.ok(Map.of("accepted", true, "subscribers", delivered));
    }
}
