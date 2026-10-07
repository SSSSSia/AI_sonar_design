package com.sonarcad.workflow.audit;

import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

/**
 * 审计日志 Mapper（T13.1，表 audit_logs 由 biz Flyway V17 建——notifications V14 同模式，
 * biz 持有 Flyway，workflow 直写同一张表）。JDBC 串带 stringtype=unspecified：detail 以
 * String 直写 jsonb 列。查询端点在 auth 服务（GET /api/v1/audit/logs）。
 */
@Mapper
public interface AuditMapper {

    @Insert("INSERT INTO audit_logs (user_id, username, action, resource_type, resource_id, detail, ip) "
            + "VALUES (#{userId}, #{username}, #{action}, #{resourceType}, #{resourceId}, #{detail}, #{ip})")
    int insert(@Param("userId") String userId, @Param("username") String username,
               @Param("action") String action, @Param("resourceType") String resourceType,
               @Param("resourceId") String resourceId, @Param("detail") String detail, @Param("ip") String ip);
}
