package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

/**
 * users 表只读视图（审查 P1-2）：会话创建人 UUID → username。
 * workflow 库与 users 同库（Flyway V1/V2 建表播种），仅查询不写入。
 */
@TableName("users")
public class UserLookupEntity {
    @TableId(type = IdType.INPUT)
    private String id;
    private String username;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
}
