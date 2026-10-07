package com.sonarcad.auth.web;

import com.sonarcad.auth.security.JwtService;
import com.sonarcad.auth.user.UserEntity;
import com.sonarcad.auth.user.UserMapper;
import com.sonarcad.auth.user.UserRoleMapper;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 用户管理 API（T11.2，P2-5 + P2-7 用户管理部分，admin-only）：
 *   GET    /api/v1/auth/users                列表（含角色）
 *   POST   /api/v1/auth/users                创建（BCrypt）
 *   PATCH  /api/v1/auth/users/{id}           启停（status）
 *   POST   /api/v1/auth/users/{id}/reset-password
 *   PUT    /api/v1/auth/users/{id}/roles     角色重设
 *   DELETE /api/v1/auth/users/{id}           删除
 * 鉴权：gateway 验签后透传 Bearer，此处用 JwtService 二次解析并要求 admin 角色
 * （auth 是认证服务本体，不信任网关注入的 X-User-Roles 头，自我校验更严）。
 * 护栏：不能停用/删除自己；不能动掉最后一个 active admin 的 admin 权限。
 */
@RestController
@RequestMapping("/api/v1/auth/users")
public class UserController {

    private static final Pattern USERNAME = Pattern.compile("^[a-zA-Z0-9_-]{3,32}$");
    private static final Set<String> KNOWN_ROLE_CODES = Set.of("admin", "designer", "reviewer", "viewer");

    /** 携带 HTTP 语义的校验异常，经 @ExceptionHandler 落平台统一响应包。 */
    private static final class ApiException extends RuntimeException {
        final int code;

        ApiException(int code, String message) {
            super(message);
            this.code = code;
        }
    }

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ApiResponse<Void>> onApiException(ApiException e) {
        HttpStatus status = HttpStatus.resolve(e.code) == null ? HttpStatus.BAD_REQUEST : HttpStatus.resolve(e.code);
        return ResponseEntity.status(status).body(ApiResponse.error(e.code, e.getMessage()));
    }

    private final UserMapper userMapper;
    private final UserRoleMapper userRoleMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    private final com.sonarcad.auth.audit.AuditRecorder auditRecorder;

    public UserController(UserMapper userMapper, UserRoleMapper userRoleMapper,
                          PasswordEncoder passwordEncoder, JwtService jwtService,
                          com.sonarcad.auth.audit.AuditRecorder auditRecorder) {
        this.userMapper = userMapper;
        this.userRoleMapper = userRoleMapper;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
        this.auditRecorder = auditRecorder;
    }

    public record CreateUserRequest(String username, String password, String displayName, String email, List<String> roles) {}
    public record PatchUserRequest(String status) {}
    public record ResetPasswordRequest(String password) {}
    public record SetRolesRequest(List<String> roles) {}

    @GetMapping
    public ApiResponse<List<Map<String, Object>>> list(@RequestHeader(value = "Authorization", required = false) String auth) {
        adminGuard(auth);
        return ApiResponse.ok(userMapper.selectList(null).stream().map(this::toView).toList());
    }

    @PostMapping
    @Transactional
    public ApiResponse<Map<String, Object>> create(@RequestHeader(value = "Authorization", required = false) String auth,
                                                   @RequestBody CreateUserRequest req) {
        JwtService.ParsedUser me = adminGuard(auth);
        if (req.username() == null || !USERNAME.matcher(req.username()).matches()) {
            throw new ApiException(400, "用户名需为 3-32 位字母/数字/下划线/中划线");
        }
        if (req.password() == null || req.password().length() < 6) {
            throw new ApiException(400, "密码至少 6 位");
        }
        List<String> roles = req.roles() == null ? List.of("designer") : req.roles();
        if (!KNOWN_ROLE_CODES.containsAll(roles)) {
            throw new ApiException(400, "非法角色，仅支持 admin/designer/reviewer/viewer");
        }
        Long exists = userMapper.selectCount(
                new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<UserEntity>()
                        .eq(UserEntity::getUsername, req.username()));
        if (exists != null && exists > 0) {
            throw new ApiException(409, "用户名已存在");
        }
        UserEntity user = new UserEntity();
        user.setUsername(req.username());
        user.setPasswordHash(passwordEncoder.encode(req.password()));
        user.setDisplayName(req.displayName() == null || req.displayName().isBlank() ? req.username() : req.displayName());
        user.setEmail(req.email());
        user.setStatus("active");
        user.setCreatedAt(OffsetDateTime.now());
        user.setUpdatedAt(OffsetDateTime.now());
        userMapper.insert(user);
        applyRoles(user.getId(), roles);
        auditRecorder.record(me.userId(), me.username(), "user.create", "user", user.getId(),
                Map.of("target", user.getUsername(), "roles", roles), null);
        return ApiResponse.ok(toView(user));
    }

    @PatchMapping("/{id}")
    public ApiResponse<Map<String, Object>> patch(@RequestHeader(value = "Authorization", required = false) String auth,
                                                  @PathVariable String id,
                                                  @RequestBody PatchUserRequest req) {
        JwtService.ParsedUser me = adminGuard(auth);
        if (req.status() == null || !Set.of("active", "disabled").contains(req.status())) {
            throw new ApiException(400, "status 仅支持 active|disabled");
        }
        UserEntity user = userMapper.selectById(id);
        if (user == null) {
            throw new ApiException(404, "用户不存在");
        }
        if (me.userId().equals(user.getId()) && "disabled".equals(req.status())) {
            throw new ApiException(400, "不能停用当前登录账号自己");
        }
        if ("disabled".equals(req.status()) && isAdmin(user) && activeAdminCountExcluding(user.getId()) == 0) {
            throw new ApiException(400, "不能停用最后一个启用中的 admin");
        }
        user.setStatus(req.status());
        user.setUpdatedAt(OffsetDateTime.now());
        userMapper.updateById(user);
        auditRecorder.record(me.userId(), me.username(), "user.patch", "user", user.getId(),
                Map.of("target", user.getUsername(), "status", req.status()), null);
        return ApiResponse.ok(toView(user));
    }

    @PostMapping("/{id}/reset-password")
    public ApiResponse<Map<String, Object>> resetPassword(@RequestHeader(value = "Authorization", required = false) String auth,
                                                          @PathVariable String id,
                                                          @RequestBody ResetPasswordRequest req) {
        JwtService.ParsedUser me = adminGuard(auth);
        if (req.password() == null || req.password().length() < 6) {
            throw new ApiException(400, "密码至少 6 位");
        }
        UserEntity user = userMapper.selectById(id);
        if (user == null) {
            throw new ApiException(404, "用户不存在");
        }
        user.setPasswordHash(passwordEncoder.encode(req.password()));
        user.setUpdatedAt(OffsetDateTime.now());
        userMapper.updateById(user);
        auditRecorder.record(me.userId(), me.username(), "user.reset_password", "user", user.getId(),
                Map.of("target", user.getUsername()), null);
        return ApiResponse.ok(Map.of("id", user.getId(), "reset", true));
    }

    @PutMapping("/{id}/roles")
    @Transactional
    public ApiResponse<Map<String, Object>> setRoles(@RequestHeader(value = "Authorization", required = false) String auth,
                                                     @PathVariable String id,
                                                     @RequestBody SetRolesRequest req) {
        JwtService.ParsedUser me = adminGuard(auth);
        if (req.roles() == null || req.roles().isEmpty() || !KNOWN_ROLE_CODES.containsAll(req.roles())) {
            throw new ApiException(400, "角色需为 admin/designer/reviewer/viewer 的非空子集");
        }
        UserEntity user = userMapper.selectById(id);
        if (user == null) {
            throw new ApiException(404, "用户不存在");
        }
        if (me.userId().equals(user.getId()) && isAdmin(user) && !req.roles().contains("admin")
                && activeAdminCountExcluding(user.getId()) == 0) {
            throw new ApiException(400, "不能移除最后一个启用中的 admin 的 admin 角色");
        }
        userRoleMapper.deleteAllForUser(user.getId());
        applyRoles(user.getId(), req.roles());
        auditRecorder.record(me.userId(), me.username(), "user.set_roles", "user", user.getId(),
                Map.of("target", user.getUsername(), "roles", req.roles()), null);
        return ApiResponse.ok(Map.of("id", user.getId(), "roles", req.roles()));
    }

    @DeleteMapping("/{id}")
    @Transactional
    public ApiResponse<Map<String, Object>> delete(@RequestHeader(value = "Authorization", required = false) String auth,
                                                   @PathVariable String id) {
        JwtService.ParsedUser me = adminGuard(auth);
        if (me.userId().equals(id)) {
            throw new ApiException(400, "不能删除当前登录账号自己");
        }
        UserEntity user = userMapper.selectById(id);
        if (user == null) {
            throw new ApiException(404, "用户不存在");
        }
        if (isAdmin(user) && activeAdminCountExcluding(user.getId()) == 0) {
            throw new ApiException(400, "不能删除最后一个启用中的 admin");
        }
        userRoleMapper.deleteAllForUser(id);
        userMapper.deleteById(id);
        auditRecorder.record(me.userId(), me.username(), "user.delete", "user", id,
                Map.of("target", user.getUsername()), null);
        return ApiResponse.ok(Map.of("id", id, "deleted", true));
    }

    // ---- helpers ----

    private Map<String, Object> toView(UserEntity user) {
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("id", user.getId());
        view.put("username", user.getUsername());
        view.put("displayName", user.getDisplayName());
        view.put("email", user.getEmail());
        view.put("status", user.getStatus());
        view.put("roles", userRoleMapper.selectRoleCodesByUserId(user.getId()));
        view.put("createdAt", user.getCreatedAt() == null ? null : user.getCreatedAt().toString());
        return view;
    }

    private void applyRoles(String userId, List<String> roles) {
        for (String code : roles) {
            String roleId = userRoleMapper.selectRoleIdByCode(code);
            if (roleId != null) {
                userRoleMapper.insert(userId, roleId);
            }
        }
    }

    private boolean isAdmin(UserEntity user) {
        return userRoleMapper.selectRoleCodesByUserId(user.getId()).contains("admin");
    }

    /** 启用中的 admin 数（排除 candidateUserId——拟停用/降权/删除的对象，剩余为 0 即「最后一个」）。 */
    private int activeAdminCountExcluding(String candidateUserId) {
        List<String> admins = userRoleMapper.selectActiveAdminUserIds();
        return (int) admins.stream().filter(uid -> !uid.equals(candidateUserId)).count();
    }

    /** admin 门禁：Bearer 二次验签 + roles 含 admin；不通过抛 ApiException（401/403）。 */
    private JwtService.ParsedUser adminGuard(String auth) {
        if (auth == null || !auth.startsWith("Bearer ")) {
            throw new ApiException(401, "缺少 Bearer Token");
        }
        try {
            JwtService.ParsedUser parsed = jwtService.parse(auth.substring(7));
            if (!parsed.roles().contains("admin")) {
                throw new ApiException(403, "该操作需要 admin 角色");
            }
            return parsed;
        } catch (ApiException e) {
            throw e;
        } catch (Exception e) {
            throw new ApiException(401, "Token 无效或已过期");
        }
    }
}
