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

    /** 按 (project_id, pipeline_status) 分组计数（项目卡片状态分布 + 总数）。 */
    @Select("select project_id, pipeline_status as status, count(*) as cnt "
            + "from design_sessions group by project_id, pipeline_status")
    List<Map<String, Object>> countSessionsByStatus();

    /** 每项目最近一次会话活动时间（max(updated_at)）。 */
    @Select("select project_id, max(updated_at) as last_at from design_sessions group by project_id")
    List<Map<String, Object>> lastSessionActivity();
}
