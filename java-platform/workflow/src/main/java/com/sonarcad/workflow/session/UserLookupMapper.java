package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

/** users 只读查询（会话创建人 username，审查 P1-2）。 */
@Mapper
public interface UserLookupMapper extends BaseMapper<UserLookupEntity> {
}
