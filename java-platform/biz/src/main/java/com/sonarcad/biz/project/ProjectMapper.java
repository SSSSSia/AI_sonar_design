package com.sonarcad.biz.project;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

@Mapper
public interface ProjectMapper extends BaseMapper<ProjectEntity> {

    /** 项目下会话数（design_sessions 与 projects 同库，删除保护用）。 */
    @Select("select count(*) from design_sessions where project_id = #{projectId}")
    long countSessions(@Param("projectId") String projectId);

    /** 全表 project_id → 会话数（列表页一次取齐，避免逐项目 N+1）。 */
    @Select("select project_id, count(*) as cnt from design_sessions group by project_id")
    List<Map<String, Object>> countSessionsGrouped();
}
