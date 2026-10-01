package com.sonarcad.notify.store;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 通知持久化（T11.1，P2-4）：关键信封落库 + 用户维度查询/已读/清理。
 * 落库类型口径（PHASE-011 §6）：review_request（待裁决）与 alert（系统告警）；
 * task_progress / log 高频过程类与 ir_updated（每会话 5 次）仅 WS 广播。
 * 落库失败静默降级（推送是增强而非权威——既有纪律，SessionWebSocketHandler 同口径）。
 */
@Service
public class NotificationService {

    private static final Logger log = LoggerFactory.getLogger(NotificationService.class);

    static final Set<String> PERSIST_TYPES = Set.of("review_request", "alert");

    /** review_request 只扇出给能裁决的角色；其余落库类型全员（null=不过滤）。 */
    private static final Map<String, List<String>> TYPE_ROLES = Map.of(
            "review_request", List.of("reviewer", "admin"));

    private final NotificationMapper mapper;
    private final ObjectMapper objectMapper;

    public NotificationService(NotificationMapper mapper, ObjectMapper objectMapper) {
        this.mapper = mapper;
        this.objectMapper = objectMapper;
    }

    /** 信封落库（扇出到 users）。任何异常只记 warn，不影响 WS 广播链路。 */
    public void persistEnvelope(Map<String, Object> envelope) {
        try {
            String type = String.valueOf(envelope.get("type"));
            if (!PERSIST_TYPES.contains(type)) {
                return;
            }
            String clientKey = envelope.get("session_id") + "|" + type + "|" + envelope.get("timestamp");
            int rows = mapper.fanOutInsert(
                    String.valueOf(envelope.getOrDefault("session_id", "")),
                    clientKey,
                    type,
                    titleOf(type, envelope.get("payload")),
                    writeJson(envelope.get("payload")),
                    TYPE_ROLES.get(type));
            if (log.isDebugEnabled()) {
                log.debug("通知落库 {} 行（type={}）", rows, type);
            }
        } catch (Exception e) {
            log.warn("通知落库失败（广播不受影响）：{}", e.toString());
        }
    }

    public List<NotificationEntity> list(String userId, int limit, int offset) {
        return mapper.listForUser(userId, limit, offset);
    }

    public long total(String userId) {
        return mapper.countForUser(userId);
    }

    public long unread(String userId) {
        return mapper.countUnread(userId);
    }

    public int markRead(String id, String userId) {
        return mapper.markRead(id, userId);
    }

    public int markAllRead(String userId) {
        return mapper.markAllRead(userId);
    }

    public int delete(String id, String userId) {
        return mapper.deleteForUser(id, userId);
    }

    public int clearRead(String userId) {
        return mapper.clearRead(userId);
    }

    public int clearAll(String userId) {
        return mapper.clearAll(userId);
    }

    /** alert 信封服务端生成标题（前端对 review_request 沿用 payload.gate 派生，不重复实现）。 */
    private String titleOf(String type, Object payload) {
        if ("alert".equals(type) && payload instanceof Map<?, ?> p) {
            Object status = p.get("status");
            Object count = p.get("count");
            return "系统告警 %s（%s 条）".formatted(status == null ? "unknown" : status,
                    count == null ? "?" : count);
        }
        return null;
    }

    private String writeJson(Object payload) {
        try {
            return objectMapper.writeValueAsString(payload == null ? Map.of() : payload);
        } catch (JsonProcessingException e) {
            return "{}";
        }
    }
}
