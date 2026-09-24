package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

/**
 * cad_tune_sessions 表只读视图（PHASE-008 T8.6）：设计会话 → 活跃微调会话状态回填。
 * 微调会话由 py-engine cad_agent 维护、与本服务同库，仅查询不写入（fillKbNames 同模式）。
 */
@TableName("cad_tune_sessions")
public class CadTuneLookupEntity {
    @TableId(type = IdType.INPUT)
    private String id;
    private String designSessionId;
    private String status;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getDesignSessionId() { return designSessionId; }
    public void setDesignSessionId(String designSessionId) { this.designSessionId = designSessionId; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
}
