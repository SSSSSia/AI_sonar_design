package com.sonarcad.biz.web;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 统一错误响应（宪法 §5.3）。 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    /** RBAC 拒绝：必须落 403 而非被兜底成 500。 */
    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ProjectController.ApiResponse<Void>> handleAccessDenied(AccessDeniedException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
                .body(new ProjectController.ApiResponse<>(403, "无权限执行该操作", null, java.util.UUID.randomUUID().toString()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ProjectController.ApiResponse<Void>> handleBadRequest(IllegalArgumentException e) {
        return ResponseEntity.badRequest()
                .body(new ProjectController.ApiResponse<>(400, e.getMessage(), null, java.util.UUID.randomUUID().toString()));
    }

    /** 业务状态异常（404/409 等）：按原状态码透出 reason，不被兜底成 500。 */
    @ExceptionHandler(org.springframework.web.server.ResponseStatusException.class)
    public ResponseEntity<ProjectController.ApiResponse<Void>> handleStatus(org.springframework.web.server.ResponseStatusException e) {
        HttpStatus status = HttpStatus.resolve(e.getStatusCode().value());
        String message = e.getReason() != null ? e.getReason()
                : (status != null ? status.getReasonPhrase() : "请求失败");
        return ResponseEntity.status(status != null ? status : HttpStatus.INTERNAL_SERVER_ERROR)
                .body(new ProjectController.ApiResponse<>(e.getStatusCode().value(), message, null, java.util.UUID.randomUUID().toString()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ProjectController.ApiResponse<Void>> handleUnexpected(Exception e) {
        return ResponseEntity.internalServerError()
                .body(new ProjectController.ApiResponse<>(500, e.getMessage(), null, java.util.UUID.randomUUID().toString()));
    }
}
