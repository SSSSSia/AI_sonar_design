package com.sonarcad.notify.store;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.OffsetDateTime;

/** notifications 表（biz Flyway V14 建表，T11.1）：广播信封按用户扇出落行，已读/删除按用户隔离。 */
@TableName("notifications")
public class NotificationEntity {

    @TableId(type = IdType.ASSIGN_UUID)
    private String id;

    private String userId;

    private String sessionId;

    /** 幂等键 = 会话|类型|信封时间戳，(user_id, client_key) 唯一，重投/重放不重复。 */
    private String clientKey;

    private String type;

    private String title;

    /** JSON 文本（jsonb 列，stringtype=unspecified 直写直读）。 */
    private String payload;

    private OffsetDateTime readAt;

    private OffsetDateTime createdAt;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getUserId() { return userId; }
    public void setUserId(String userId) { this.userId = userId; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
    public String getClientKey() { return clientKey; }
    public void setClientKey(String clientKey) { this.clientKey = clientKey; }
    public String getType() { return type; }
    public void setType(String type) { this.type = type; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getPayload() { return payload; }
    public void setPayload(String payload) { this.payload = payload; }
    public OffsetDateTime getReadAt() { return readAt; }
    public void setReadAt(OffsetDateTime readAt) { this.readAt = readAt; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
}
