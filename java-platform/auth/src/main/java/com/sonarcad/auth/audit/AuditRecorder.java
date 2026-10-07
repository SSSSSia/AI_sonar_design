package com.sonarcad.auth.audit;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * 审计旁路记录器（T13.1，§9.4 审计留痕）：落库失败只打 log 不阻断主流程——审计是旁路，
 * 不能因它拖垮业务动作。detail 只放动作摘要（不含密码/密钥明文，宪法 §10）。
 * action 命名 `域.动作`（auth.login / user.create / session.delete / review.decide / project.create …）。
 */
@Component
public class AuditRecorder {

    private static final Logger log = LoggerFactory.getLogger(AuditRecorder.class);
    private static final ObjectMapper JSON = new ObjectMapper();

    private final AuditMapper mapper;

    public AuditRecorder(AuditMapper mapper) {
        this.mapper = mapper;
    }

    public void record(String userId, String username, String action,
                       String resourceType, String resourceId, Map<String, Object> detail, String ip) {
        try {
            mapper.insert(userId, username, action, resourceType, resourceId,
                    detail == null ? null : JSON.writeValueAsString(detail), ip);
        } catch (Exception e) {
            log.warn("审计落库失败 action={}（旁路忽略）: {}", action, e.getMessage());
        }
    }
}
