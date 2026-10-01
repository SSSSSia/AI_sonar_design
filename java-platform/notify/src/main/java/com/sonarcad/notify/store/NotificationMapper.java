package com.sonarcad.notify.store;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.List;

@Mapper
public interface NotificationMapper extends BaseMapper<NotificationEntity> {

    /**
     * 扇出落库：广播信封按 users 表逐活跃用户落行（T11.1）。
     * roles 非空时仅扇出给拥有这些角色的用户（review_request 只给能裁决的人）；null = 全员。
     * (user_id, client_key) 唯一冲突即忽略——重投/重放幂等。
     */
    @Insert("<script>"
            + "INSERT INTO notifications (user_id, session_id, client_key, type, title, payload) "
            + "SELECT u.id, #{sessionId}, #{clientKey}, #{type}, #{title}, #{payload} "
            + "FROM users u WHERE u.status = 'active' "
            + "<if test='roles != null'>"
            + "AND EXISTS (SELECT 1 FROM user_roles ur JOIN roles ro ON ro.id = ur.role_id "
            + "WHERE ur.user_id = u.id AND ro.code IN "
            + "<foreach collection='roles' item='r' open='(' separator=',' close=')'>#{r}</foreach>) "
            + "</if>"
            + "ON CONFLICT (user_id, client_key) DO NOTHING"
            + "</script>")
    int fanOutInsert(@Param("sessionId") String sessionId, @Param("clientKey") String clientKey,
                     @Param("type") String type, @Param("title") String title,
                     @Param("payload") String payload, @Param("roles") List<String> roles);

    @Select("SELECT * FROM notifications WHERE user_id = #{userId} "
            + "ORDER BY created_at DESC LIMIT #{limit} OFFSET #{offset}")
    List<NotificationEntity> listForUser(@Param("userId") String userId,
                                         @Param("limit") int limit, @Param("offset") int offset);

    @Select("SELECT COUNT(*) FROM notifications WHERE user_id = #{userId}")
    long countForUser(@Param("userId") String userId);

    @Select("SELECT COUNT(*) FROM notifications WHERE user_id = #{userId} AND read_at IS NULL")
    long countUnread(@Param("userId") String userId);

    @Update("UPDATE notifications SET read_at = now() WHERE id = #{id} AND user_id = #{userId} AND read_at IS NULL")
    int markRead(@Param("id") String id, @Param("userId") String userId);

    @Update("UPDATE notifications SET read_at = now() WHERE user_id = #{userId} AND read_at IS NULL")
    int markAllRead(@Param("userId") String userId);

    @Delete("DELETE FROM notifications WHERE id = #{id} AND user_id = #{userId}")
    int deleteForUser(@Param("id") String id, @Param("userId") String userId);

    @Delete("DELETE FROM notifications WHERE user_id = #{userId} AND read_at IS NOT NULL")
    int clearRead(@Param("userId") String userId);

    @Delete("DELETE FROM notifications WHERE user_id = #{userId}")
    int clearAll(@Param("userId") String userId);
}
