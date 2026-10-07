package com.sonarcad.workflow.audit;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * 审计旁路记录器（T13.1，§9.4）：落库失败只打 log 不阻断主流程。
 * workflow 侧信任头仅有 X-User-Id（无用户名），username 传 null——查询端点 LEFT JOIN users 补齐。
 */
@Component
public class AuditRecorder {

    private static final Logger log = LoggerFactory.getLogger(AuditRecorder.class);
    private static final ObjectMapper JSON = new ObjectMapper();

    private final AuditMapper mapper;

    public AuditRecorder(AuditMapper mapper) {
        this.mapper = mapper;
    }

    public void record(String userId, String action,
                       String resourceType, String resourceId, Map<String, Object> detail, String ip) {
        try {
            mapper.insert(userId, null, action, resourceType, resourceId,
                    detail == null ? null : JSON.writeValueAsString(detail), ip);
        } catch (Exception e) {
            log.warn("审计落库失败 action={}（旁路忽略）: {}", action, e.getMessage());
        }
    }
}
