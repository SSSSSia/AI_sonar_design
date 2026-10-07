package com.sonarcad.auth.audit;

import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

/**
 * 审计日志 Mapper（T13.1，表 audit_logs 由 biz Flyway V17 建，T13.1 同库多服务直写——notifications V14 同模式）。
 * JDBC 串带 stringtype=unspecified：detail 以 String 直写 jsonb 列。
 */
@Mapper
public interface AuditMapper {

    @Insert("INSERT INTO audit_logs (user_id, username, action, resource_type, resource_id, detail, ip) "
            + "VALUES (#{userId}, #{username}, #{action}, #{resourceType}, #{resourceId}, #{detail}, #{ip})")
    int insert(@Param("userId") String userId, @Param("username") String username,
               @Param("action") String action, @Param("resourceType") String resourceType,
               @Param("resourceId") String resourceId, @Param("detail") String detail, @Param("ip") String ip);

    String SEARCH_WHERE = "<where>"
            + " <if test='username != null'>AND COALESCE(a.username, u.username) ILIKE ('%' || CAST(#{username} AS text) || '%')</if>"
            + " <if test='action != null'>AND a.action ILIKE (CAST(#{action} AS text) || '%')</if>"
            + " <if test='resourceType != null'>AND a.resource_type = CAST(#{resourceType} AS text)</if>"
            + " <if test='hours != null'>AND a.created_at &gt;= now() - make_interval(hours =&gt; CAST(#{hours} AS int))</if>"
            + "</where>";

    /** 检索（admin）：LEFT JOIN users 补齐写入方未携带的用户名；detail 以文本回传（控制器转 JSON 对象）。 */
    @Select("<script>"
            + "SELECT a.id, a.user_id, COALESCE(a.username, u.username) AS username, a.action, "
            + "a.resource_type, a.resource_id, a.detail::text AS detail, a.ip, "
            + "to_char(a.created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS') AS created_at "
            + "FROM audit_logs a LEFT JOIN users u ON u.id::text = a.user_id "
            + SEARCH_WHERE
            + " ORDER BY a.id DESC LIMIT #{limit} OFFSET #{offset}"
            + "</script>")
    List<Map<String, Object>> search(@Param("username") String username, @Param("action") String action,
                                     @Param("resourceType") String resourceType, @Param("hours") Integer hours,
                                     @Param("limit") int limit, @Param("offset") long offset);

    @Select("<script>"
            + "SELECT COUNT(*) FROM audit_logs a LEFT JOIN users u ON u.id::text = a.user_id "
            + SEARCH_WHERE
            + "</script>")
    long count(@Param("username") String username, @Param("action") String action,
               @Param("resourceType") String resourceType, @Param("hours") Integer hours);
}
