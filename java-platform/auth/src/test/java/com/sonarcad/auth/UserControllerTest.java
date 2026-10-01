package com.sonarcad.auth;

import com.sonarcad.auth.security.JwtService;
import com.sonarcad.auth.user.UserEntity;
import com.sonarcad.auth.user.UserMapper;
import com.sonarcad.auth.user.UserRoleMapper;
import com.sonarcad.auth.web.ApiResponse;
import com.sonarcad.auth.web.UserController;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * T11.2 用户管理 API 单测：admin 门禁（Bearer 二次验签）、CRUD 流与最后 admin 护栏。
 * 校验失败路径抛 ApiException（由 @ExceptionHandler 落统一响应包，此处断言异常语义）；
 * 纯 Mockito 单测（无 Spring 上下文/无 DB）；SQL 正确性由容器栈实机验证承接。
 */
class UserControllerTest {

    private static final SecretKey KEY = Keys.hmacShaKeyFor(
            "test-secret-auth-0123456789abcdef0123456789abcdef".getBytes(StandardCharsets.UTF_8));
    private static final JwtService JWT = new JwtService(
            "test-secret-auth-0123456789abcdef0123456789abcdef", 1);

    private UserMapper userMapper;
    private UserRoleMapper userRoleMapper;
    private UserController controller;

    @BeforeEach
    void setUp() {
        userMapper = mock(UserMapper.class);
        userRoleMapper = mock(UserRoleMapper.class);
        PasswordEncoder encoder = new BCryptPasswordEncoder();
        controller = new UserController(userMapper, userRoleMapper, encoder, JWT);
    }

    private String token(String... roles) {
        Date now = new Date();
        return Jwts.builder()
                .subject("11111111-1111-1111-1111-111111111111")
                .claim("username", "root")
                .claim("roles", List.of(roles))
                .issuedAt(now)
                .expiration(new Date(now.getTime() + 600_000))
                .signWith(KEY)
                .compact();
    }

    private UserEntity user(String id, String username, String status) {
        UserEntity u = new UserEntity();
        u.setId(id);
        u.setUsername(username);
        u.setDisplayName(username);
        u.setStatus(status);
        return u;
    }

    // ---- 门禁 ----

    @Test
    void listRequiresAdminRole() {
        assertThatThrownBy(() -> controller.list("Bearer " + token("designer")))
                .hasMessage("该操作需要 admin 角色");
        assertThatThrownBy(() -> controller.list(null))
                .hasMessage("缺少 Bearer Token");
        assertThatThrownBy(() -> controller.list("Bearer garbage-token"))
                .hasMessage("Token 无效或已过期");
        verify(userMapper, never()).selectList(any());
    }

    @Test
    void listReturnsUsersForAdmin() {
        when(userMapper.selectList(any())).thenReturn(List.of(user("u-1", "alice", "active")));
        when(userRoleMapper.selectRoleCodesByUserId("u-1")).thenReturn(List.of("designer"));
        ApiResponse<List<Map<String, Object>>> resp = controller.list("Bearer " + token("admin"));
        assertThat(resp.code()).isEqualTo(0);
        assertThat(resp.data()).hasSize(1);
        assertThat(resp.data().get(0)).containsEntry("username", "alice").containsEntry("roles", List.of("designer"));
    }

    @Test
    void createRejectsBadUsernameAndShortPassword() {
        assertThatThrownBy(() -> controller.create("Bearer " + token("admin"),
                new UserController.CreateUserRequest("ab", "123456", null, null, null)))
                .hasMessage("用户名需为 3-32 位字母/数字/下划线/中划线");
        assertThatThrownBy(() -> controller.create("Bearer " + token("admin"),
                new UserController.CreateUserRequest("abc", "12345", null, null, null)))
                .hasMessage("密码至少 6 位");
        verify(userMapper, never()).insert(any(UserEntity.class));
    }

    @Test
    void createHashesPasswordAndAppliesRoles() {
        when(userMapper.selectCount(any())).thenReturn(0L);
        when(userRoleMapper.selectRoleIdByCode("designer")).thenReturn("role-designer");
        ApiResponse<Map<String, Object>> resp = controller.create(
                "Bearer " + token("admin"),
                new UserController.CreateUserRequest("alice", "secret123", "Alice", null, List.of("designer")));
        assertThat(resp.code()).isEqualTo(0);
        verify(userMapper).insert(any(UserEntity.class));
        // 纯单测无 MyBatis-Plus 运行时，ASSIGN_UUID 不生效 → userId 为 null，用 any() 匹配
        verify(userRoleMapper).insert(any(), eq("role-designer"));
        assertThat(resp.data().get("username")).isEqualTo("alice");
    }

    @Test
    void createRejectsUnknownRole() {
        assertThatThrownBy(() -> controller.create("Bearer " + token("admin"),
                new UserController.CreateUserRequest("alice", "secret123", null, null, List.of("superuser"))))
                .hasMessage("非法角色，仅支持 admin/designer/reviewer/viewer");
    }

    // ---- 护栏 ----

    @Test
    void cannotDisableSelf() {
        String selfId = "11111111-1111-1111-1111-111111111111";
        when(userMapper.selectById(selfId)).thenReturn(user(selfId, "root", "active"));
        when(userRoleMapper.selectRoleCodesByUserId(selfId)).thenReturn(List.of("admin"));
        assertThatThrownBy(() -> controller.patch("Bearer " + token("admin"), selfId,
                new UserController.PatchUserRequest("disabled")))
                .hasMessage("不能停用当前登录账号自己");
        verify(userMapper, never()).updateById(any(UserEntity.class));
    }

    @Test
    void cannotDisableLastActiveAdmin() {
        String adminId = "22222222-2222-2222-2222-222222222222";
        when(userMapper.selectById(adminId)).thenReturn(user(adminId, "admin2", "active"));
        when(userRoleMapper.selectRoleCodesByUserId(adminId)).thenReturn(List.of("admin"));
        // 排除被操作者之后 active admin = 0（selectActiveAdminUserIds 只返回该用户自己）
        when(userRoleMapper.selectActiveAdminUserIds()).thenReturn(List.of(adminId));
        assertThatThrownBy(() -> controller.patch("Bearer " + token("admin"), adminId,
                new UserController.PatchUserRequest("disabled")))
                .hasMessage("不能停用最后一个启用中的 admin");
    }

    @Test
    void disableNonLastAdminSucceeds() {
        String adminId = "22222222-2222-2222-2222-222222222222";
        when(userMapper.selectById(adminId)).thenReturn(user(adminId, "admin2", "active"));
        when(userRoleMapper.selectRoleCodesByUserId(adminId)).thenReturn(List.of("admin"));
        when(userRoleMapper.selectActiveAdminUserIds())
                .thenReturn(List.of("11111111-1111-1111-1111-111111111111", adminId));
        ApiResponse<Map<String, Object>> resp = controller.patch("Bearer " + token("admin"), adminId,
                new UserController.PatchUserRequest("disabled"));
        assertThat(resp.code()).isEqualTo(0);
        verify(userMapper).updateById(any(UserEntity.class));
    }

    @Test
    void resetPasswordEncodes() {
        String uid = "33333333-3333-3333-3333-333333333333";
        when(userMapper.selectById(uid)).thenReturn(user(uid, "bob", "active"));
        ApiResponse<Map<String, Object>> resp = controller.resetPassword("Bearer " + token("admin"), uid,
                new UserController.ResetPasswordRequest("newpass66"));
        assertThat(resp.code()).isEqualTo(0);
        verify(userMapper).updateById(any(UserEntity.class));
    }

    @Test
    void setRolesReplacesAllRoles() {
        String uid = "33333333-3333-3333-3333-333333333333";
        when(userMapper.selectById(uid)).thenReturn(user(uid, "bob", "active"));
        when(userRoleMapper.selectRoleCodesByUserId(uid)).thenReturn(List.of("viewer"));
        when(userRoleMapper.selectRoleIdByCode("reviewer")).thenReturn("role-reviewer");
        ApiResponse<Map<String, Object>> resp = controller.setRoles("Bearer " + token("admin"), uid,
                new UserController.SetRolesRequest(List.of("reviewer")));
        assertThat(resp.code()).isEqualTo(0);
        verify(userRoleMapper).deleteAllForUser(uid);
        verify(userRoleMapper).insert(uid, "role-reviewer");
    }

    @Test
    void deleteRejectsSelfAndLastAdmin() {
        String selfId = "11111111-1111-1111-1111-111111111111";
        assertThatThrownBy(() -> controller.delete("Bearer " + token("admin"), selfId))
                .hasMessage("不能删除当前登录账号自己");

        String lastAdmin = "22222222-2222-2222-2222-222222222222";
        when(userMapper.selectById(lastAdmin)).thenReturn(user(lastAdmin, "admin2", "active"));
        when(userRoleMapper.selectRoleCodesByUserId(lastAdmin)).thenReturn(List.of("admin"));
        when(userRoleMapper.selectActiveAdminUserIds()).thenReturn(List.of(lastAdmin));
        assertThatThrownBy(() -> controller.delete("Bearer " + token("admin"), lastAdmin))
                .hasMessage("不能删除最后一个启用中的 admin");
        verify(userMapper, never()).deleteById(anyString());
    }
}
