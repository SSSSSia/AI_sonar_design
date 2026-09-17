package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.OffsetDateTime;

/** traceability_chain 表（V1 建表）：trace_id 溯源链路步骤（T2.4）。payload 为 jsonb（读作字符串）。 */
@TableName("traceability_chain")
public class TraceChainEntity {
    @TableId(type = IdType.ASSIGN_UUID)
    private String id;
    private String traceId;
    private Integer stepNo;
    private String sessionId;
    private String actor;
    private String action;
    private String irSnapshotId;
    private String payload;
    private OffsetDateTime createdAt;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getTraceId() { return traceId; }
    public void setTraceId(String traceId) { this.traceId = traceId; }
    public Integer getStepNo() { return stepNo; }
    public void setStepNo(Integer stepNo) { this.stepNo = stepNo; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
    public String getActor() { return actor; }
    public void setActor(String actor) { this.actor = actor; }
    public String getAction() { return action; }
    public void setAction(String action) { this.action = action; }
    public String getIrSnapshotId() { return irSnapshotId; }
    public void setIrSnapshotId(String irSnapshotId) { this.irSnapshotId = irSnapshotId; }
    public String getPayload() { return payload; }
    public void setPayload(String payload) { this.payload = payload; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(OffsetDateTime createdAt) { this.createdAt = createdAt; }
}
