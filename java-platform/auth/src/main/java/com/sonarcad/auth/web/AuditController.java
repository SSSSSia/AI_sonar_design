package com.sonarcad.auth.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sonarcad.auth.audit.AuditMapper;
import com.sonarcad.auth.security.JwtService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 审计检索 API（T13.1，§9.4 审计留痕查询面，admin-only）：
 *   GET /api/v1/audit/logs?username=&action=&resource_type=&hours=&limit=&offset=
 * auth 是认证服务本体：不信任网关注入头，Bearer 二次验签要求 admin（与 UserController 同口径）。
 * detail 以 jsonb 文本回传，此处解析为 JSON 对象（解析失败原样字符串，A-16 不掩盖）。
 */
@RestController
@RequestMapping("/api/v1/audit")
public class AuditController {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final AuditMapper auditMapper;
    private final JwtService jwtService;

    public AuditController(AuditMapper auditMapper, JwtService jwtService) {
        this.auditMapper = auditMapper;
        this.jwtService = jwtService;
    }

    /** 携带 HTTP 语义的校验异常，经 @ExceptionHandler 落平台统一响应包（UserController 同模式）。 */
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

    @GetMapping("/logs")
    public ApiResponse<Map<String, Object>> logs(
            @RequestHeader(value = "Authorization", required = false) String auth,
            @RequestParam(required = false) String username,
            @RequestParam(required = false) String action,
            @RequestParam(name = "resource_type", required = false) String resourceType,
            @RequestParam(required = false) Integer hours,
            @RequestParam(defaultValue = "50") int limit,
            @RequestParam(defaultValue = "0") long offset) {
        adminGuard(auth);
        int capped = Math.min(Math.max(limit, 1), 500);
        var rows = auditMapper.search(username, action, resourceType, hours, capped, offset);
        for (Map<String, Object> row : rows) {
            Object detail = row.get("detail");
            if (detail instanceof String s && !s.isBlank()) {
                try {
                    row.put("detail", JSON.readValue(s, Map.class));
                } catch (Exception ignored) {
                    // 保持原字符串（A-16：不掩盖解析失败）
                }
            }
        }
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("total", auditMapper.count(username, action, resourceType, hours));
        data.put("logs", rows);
        return ApiResponse.ok(data);
    }

    private void adminGuard(String auth) {
        if (auth == null || !auth.startsWith("Bearer ")) {
            throw new ApiException(401, "缺少 Bearer Token");
        }
        try {
            JwtService.ParsedUser parsed = jwtService.parse(auth.substring(7));
            if (!parsed.roles().contains("admin")) {
                throw new ApiException(403, "该操作需要 admin 角色");
            }
        } catch (ApiException e) {
            throw e;
        } catch (Exception e) {
            throw new ApiException(401, "Token 无效或已过期");
        }
    }
}
