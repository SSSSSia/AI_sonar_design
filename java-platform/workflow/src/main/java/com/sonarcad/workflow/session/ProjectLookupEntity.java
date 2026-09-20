package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

/**
 * projects 表只读视图（2026-09-20 仪表盘项目维度）：会话 project_id → 项目名。
 * projects 由 biz 服务维护、与 workflow 同库，仅查询不写入。
 */
@TableName("projects")
public class ProjectLookupEntity {
    @TableId(type = IdType.INPUT)
    private String id;
    private String name;
    private String code;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getCode() { return code; }
    public void setCode(String code) { this.code = code; }
}
