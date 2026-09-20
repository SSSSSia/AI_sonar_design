package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.OffsetDateTime;

/** design_sessions 表（V1 建表，P3 §5.8）：五步主线实例，状态由编排状态机维护（D-05）。 */
@TableName("design_sessions")
public class DesignSessionEntity {
    @TableId(type = IdType.ASSIGN_UUID)
    private String id;
    private String projectId;
    private String title;
    private String pipelineStatus;
    private String currentStage;
    private String createdBy;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;

    /** 非表列：创建人用户名（查询时显式带出；MyBatis-Plus 按 getter 序列化，无 setter 不参与写入）。 */
    @com.baomidou.mybatisplus.annotation.TableField(exist = false)
    private String createdByName;

    /** 非表列：所属项目名（projects 由 biz 维护、同库只读 join 回填）。 */
    @com.baomidou.mybatisplus.annotation.TableField(exist = false)
    private String projectName;

    public String getCreatedByName() { return createdByName; }
    public void setCreatedByName(String createdByName) { this.createdByName = createdByName; }
    public String getProjectName() { return projectName; }
    public void setProjectName(String projectName) { this.projectName = projectName; }

    public String getId() { return id; }

    /** 归一化去横线（T4.2）：id 列为原生 uuid，PG 读回归一化为带横线形态——统一为 32 位无横线，保证 API/WS 频道键一致。 */
    public void setId(String id) { this.id = id == null ? null : id.replace("-", ""); }
    public String getProjectId() { return projectId; }
    public void setProjectId(String projectId) { this.projectId = projectId; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getPipelineStatus() { return pipelineStatus; }
    public void setPipelineStatus(String pipelineStatus) { this.pipelineStatus = pipelineStatus; }
    public String getCurrentStage() { return currentStage; }
    public void setCurrentStage(String currentStage) { this.currentStage = currentStage; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String createdBy) { this.createdBy = createdBy; }
    public OffsetDateTime getCreatedAt() { return createdAt; }
    public OffsetDateTime getUpdatedAt() { return updatedAt; }
}
