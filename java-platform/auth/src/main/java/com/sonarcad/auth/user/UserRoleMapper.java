package com.sonarcad.auth.user;

import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

@Mapper
public interface UserRoleMapper {

    @Select("""
            SELECT r.code FROM roles r
            JOIN user_roles ur ON ur.role_id = r.id
            WHERE ur.user_id = #{userId}
            """)
    List<String> selectRoleCodesByUserId(@Param("userId") String userId);

    @Select("SELECT id FROM roles WHERE code = #{code}")
    String selectRoleIdByCode(@Param("code") String code);

    @Select("""
            SELECT ro.code FROM roles ro
            JOIN user_roles ur ON ur.role_id = ro.id
            JOIN users u ON u.id = ur.user_id
            WHERE ro.code = 'admin' AND u.status = 'active'
            """)
    List<String> selectActiveAdminUserIds();

    @Insert("INSERT INTO user_roles (user_id, role_id) VALUES (#{userId}, #{roleId}) ON CONFLICT DO NOTHING")
    int insert(@Param("userId") String userId, @Param("roleId") String roleId);

    @Delete("DELETE FROM user_roles WHERE user_id = #{userId}")
    int deleteAllForUser(@Param("userId") String userId);
}
