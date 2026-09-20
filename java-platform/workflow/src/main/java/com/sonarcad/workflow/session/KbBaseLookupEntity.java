package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

/**
 * kb_bases 表只读视图（PHASE-006 会话选库）：kb_ids 存在性校验 + 库名回填。
 * kb_bases 由 py-engine 管线写入（V9 建 DDL 由 biz 迁移承担）、与 workflow 同库，仅查询不写入。
 */
@TableName("kb_bases")
public class KbBaseLookupEntity {
    @TableId(type = IdType.INPUT)
    private String id;
    private String name;
    private Boolean isDefault;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public Boolean getIsDefault() { return isDefault; }
    public void setIsDefault(Boolean isDefault) { this.isDefault = isDefault; }
}
