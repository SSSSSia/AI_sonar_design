package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.OffsetDateTime;

/** review_records 表（V1 建表）：审阅关①②与知识审阅关的裁决记录（T2.2）。 */
@TableName("review_records")
public class ReviewRecordEntity {
    @TableId(type = IdType.ASSIGN_UUID)
    private String id;
    private String sessionId;
    /** gate1（IR2 后）| gate2（IR3+IR4 后）| knowledge（IR5 回写确认） */
    private String gate;
    /** 被审主快照：gate1→IR2、gate2→IR3、knowledge→IR5 */
    private String irSnapshotId;
    /** approved | rejected */
    private String decision;
    /** 批注与裁决上下文（JSON 数组/对象字符串，写入 jsonb） */
    private String annotations;
    private String reviewerId;
    private OffsetDateTime decidedAt;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getSessionId() { return sessionId; }
    public void setSessionId(String sessionId) { this.sessionId = sessionId; }
    public String getGate() { return gate; }
    public void setGate(String gate) { this.gate = gate; }
    public String getIrSnapshotId() { return irSnapshotId; }
    public void setIrSnapshotId(String irSnapshotId) { this.irSnapshotId = irSnapshotId; }
    public String getDecision() { return decision; }
    public void setDecision(String decision) { this.decision = decision; }
    public String getAnnotations() { return annotations; }
    public void setAnnotations(String annotations) { this.annotations = annotations; }
    public String getReviewerId() { return reviewerId; }
    public void setReviewerId(String reviewerId) { this.reviewerId = reviewerId; }
    public OffsetDateTime getDecidedAt() { return decidedAt; }
    public void setDecidedAt(OffsetDateTime decidedAt) { this.decidedAt = decidedAt; }
}
