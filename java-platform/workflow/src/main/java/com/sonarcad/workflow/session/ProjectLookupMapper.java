package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/** projects 只读查询（会话所属项目名）。 */
@Mapper
public interface ProjectLookupMapper extends BaseMapper<ProjectLookupEntity> {
}
