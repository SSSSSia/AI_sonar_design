package com.sonarcad.auth.web;

import com.sonarcad.auth.security.JwtService;
import com.sonarcad.auth.user.UserEntity;
import com.sonarcad.auth.user.UserMapper;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 认证端点（PHASE-001 §3.2）：
 *   POST /api/v1/auth/login  {username, password} → JWT（roles claim）
 *   GET  /api/v1/auth/me     → Bearer 自省（用户 + 角色）
 */
@RestController
@RequestMapping("/api/v1/auth")
public class AuthController {

    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;

    public AuthController(UserMapper userMapper, PasswordEncoder passwordEncoder, JwtService jwtService) {
        this.userMapper = userMapper;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
    }

    public record LoginRequest(String username, String password) {}

    @PostMapping("/login")
    public ResponseEntity<ApiResponse<Map<String, Object>>> login(@RequestBody LoginRequest req) {
        UserEntity user = userMapper.selectOne(
                new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<UserEntity>()
                        .eq(UserEntity::getUsername, req.username()));
        if (user == null || !passwordEncoder.matches(req.password(), user.getPasswordHash())) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(ApiResponse.error(401, "用户名或密码错误"));
        }
        if (!"active".equals(user.getStatus())) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN)
                    .body(ApiResponse.error(403, "用户已禁用"));
        }
        List<String> roles = userMapper.selectRoleCodes(user.getUsername());
        String token = jwtService.generate(user.getId(), user.getUsername(), roles);
        return ResponseEntity.ok(ApiResponse.ok(Map.of(
                "token", token,
                "username", user.getUsername(),
                "displayName", user.getDisplayName() == null ? user.getUsername() : user.getDisplayName(),
                "roles", roles)));
    }

    @GetMapping("/me")
    public ResponseEntity<ApiResponse<Map<String, Object>>> me(@RequestHeader(value = "Authorization", required = false) String auth) {
        if (auth == null || !auth.startsWith("Bearer ")) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(ApiResponse.error(401, "缺少 Bearer Token"));
        }
        try {
            JwtService.ParsedUser parsed = jwtService.parse(auth.substring(7));
            return ResponseEntity.ok(ApiResponse.ok(Map.of(
                    "userId", parsed.userId(),
                    "username", parsed.username(),
                    "roles", parsed.roles())));
        } catch (Exception e) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(ApiResponse.error(401, "Token 无效或已过期"));
        }
    }
}
