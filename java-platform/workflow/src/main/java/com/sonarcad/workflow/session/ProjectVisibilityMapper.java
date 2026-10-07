package com.sonarcad.workflow.session;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

/**
 * 项目成员可见性查询（T13.4，表 project_members 由 biz Flyway V18 建，workflow 只读）。
 * 可见项目 = 本人 owner 的项目 ∪ 本人被授为成员的项目（§9.4「项目级」+ 附录 C「本人项目」）。
 */
@Mapper
public interface ProjectVisibilityMapper {

    @Select("SELECT id::text FROM projects WHERE owner_id = #{userId}::uuid "
            + "UNION "
            + "SELECT project_id::text FROM project_members WHERE user_id = #{userId}::uuid")
    List<String> visibleProjectIds(@Param("userId") String userId);

    @Select("SELECT EXISTS (SELECT 1 FROM projects WHERE id = #{projectId}::uuid AND owner_id = #{userId}::uuid) "
            + "OR EXISTS (SELECT 1 FROM project_members WHERE project_id = #{projectId}::uuid AND user_id = #{userId}::uuid)")
    boolean canSeeProject(@Param("projectId") String projectId, @Param("userId") String userId);
}
