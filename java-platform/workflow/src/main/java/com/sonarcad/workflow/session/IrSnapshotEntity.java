package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.OffsetDateTime;

/** ir_snapshots 表（V1 建表，宪法 A-12 不可变快照）。content 以 JSON 字符串写入 jsonb 列。 */
@TableName("ir_snapshots")
public class IrSnapshotEntity {
    @TableId(type = IdType.ASSIGN_UUID)
    private String id;
    private String sessionId;
    private String irType;
    private Integer version;
    private String schemaVersion;
    private String content;
    private String createdBy;
    private OffsetDateTime createdAt;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
    public String getIrType() { return irType; }
    public void setIrType(String irType) { this.irType = irType; }
    public Integer getVersion() { return version; }
    public void setVersion(Integer version) { this.version = version; }
    public String getSchemaVersion() { return schemaVersion; }
    public void setSchemaVersion(String schemaVersion) { this.schemaVersion = schemaVersion; }
    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String createdBy) { this.createdBy = createdBy; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
}
