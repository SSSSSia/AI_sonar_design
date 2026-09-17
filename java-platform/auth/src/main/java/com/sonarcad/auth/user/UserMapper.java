package com.sonarcad.auth.user;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

@Mapper
public interface UserMapper extends BaseMapper<UserEntity> {

    /** 用户角色 code 列表（user_roles → roles 联查） */
    @Select("""
            SELECT r.code FROM roles r
            JOIN user_roles ur ON ur.role_id = r.id
            JOIN users u ON u.id = ur.user_id
            WHERE u.username = #{username}
            """)
    List<String> selectRoleCodes(@Param("username") String username);
}
