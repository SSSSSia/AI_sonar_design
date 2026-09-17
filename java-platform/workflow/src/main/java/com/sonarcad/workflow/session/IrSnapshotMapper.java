package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.List;

@Mapper
public interface IrSnapshotMapper extends BaseMapper<IrSnapshotEntity> {

    @Select("SELECT COALESCE(MAX(version), 0) FROM ir_snapshots WHERE session_id = #{sessionId} AND ir_type = #{irType}")
    int nextVersion(@Param("sessionId") String sessionId, @Param("irType") String irType);

    /** 下游失效（T2.2，A-12）：只标 invalidated_at 元数据，内容不可变。 */
    @Update("<script>"
            + "UPDATE ir_snapshots SET invalidated_at = now() "
            + "WHERE session_id = #{sessionId} AND invalidated_at IS NULL "
            + "AND ir_type IN "
            + "<foreach collection='irTypes' item='t' open='(' separator=',' close=')'>#{t}</foreach>"
            + "</script>")
    int markInvalidated(@Param("sessionId") String sessionId, @Param("irTypes") List<String> irTypes);
}
