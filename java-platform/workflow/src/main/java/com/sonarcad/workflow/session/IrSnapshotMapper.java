package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface IrSnapshotMapper extends BaseMapper<IrSnapshotEntity> {

    @Select("SELECT COALESCE(MAX(version), 0) FROM ir_snapshots WHERE session_id = #{sessionId} AND ir_type = #{irType}")
    int nextVersion(@Param("sessionId") String sessionId, @Param("irType") String irType);
}
