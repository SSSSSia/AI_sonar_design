package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/** cad_tune_sessions 只读查询（PHASE-008 微调状态回填）。 */
@Mapper
public interface CadTuneLookupMapper extends BaseMapper<CadTuneLookupEntity> {
}
