package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/** kb_bases 只读查询（会话选库校验与库名回填，PHASE-006）。 */
@Mapper
public interface KbBaseLookupMapper extends BaseMapper<KbBaseLookupEntity> {
}
