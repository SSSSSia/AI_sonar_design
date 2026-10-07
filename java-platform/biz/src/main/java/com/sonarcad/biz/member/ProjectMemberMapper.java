package com.sonarcad.biz.member;

import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

/**
 * 项目成员 Mapper（T13.4，表 project_members 由本服务 Flyway V18 建）。
 * 列表联 users 取显示名（biz 侧无用户管理 API，读库即可）。
 */
@Mapper
public interface ProjectMemberMapper {

    @Select("SELECT pm.user_id, pm.role, pm.created_at, u.username, u.display_name "
            + "FROM project_members pm LEFT JOIN users u ON u.id = pm.user_id "
            + "WHERE pm.project_id = #{projectId} ORDER BY pm.created_at")
    List<Map<String, Object>> listForProject(@Param("projectId") String projectId);

    @Select("SELECT role FROM project_members WHERE project_id = #{projectId} AND user_id = #{userId}")
    String memberRole(@Param("projectId") String projectId, @Param("userId") String userId);

    @Select("SELECT u.id FROM users u WHERE u.username = #{username}")
    String findUserIdByUsername(@Param("username") String username);

    @Insert("INSERT INTO project_members (project_id, user_id, role) VALUES (#{projectId}, #{userId}, #{role}) "
            + "ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role")
    int upsert(@Param("projectId") String projectId, @Param("userId") String userId, @Param("role") String role);

    @Delete("DELETE FROM project_members WHERE project_id = #{projectId} AND user_id = #{userId} AND role <> 'owner'")
    int deleteMember(@Param("projectId") String projectId, @Param("userId") String userId);

    @Delete("DELETE FROM project_members WHERE project_id = #{projectId}")
    int deleteAllForProject(@Param("projectId") String projectId);
}
