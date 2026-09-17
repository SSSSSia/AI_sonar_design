package com.sonarcad.workflow.web;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.server.ResponseStatusException;

/** 统一错误响应（宪法 §5.3）：4xx 客户端 / 5xx 服务端，body 走统一响应包。 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    /** RBAC 拒绝（T2.2）：必须落 403 而非被兜底成 500（设计者无裁决权等场景）。 */
    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ApiResponse<Void>> handleAccessDenied(AccessDeniedException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
                .body(ApiResponse.error(403, "无权限执行该操作"));
    }

    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<ApiResponse<Void>> handleStatus(ResponseStatusException e) {
        HttpStatus status = e.getStatusCode().is4xxClientError() || e.getStatusCode().is5xxServerError()
                ? HttpStatus.valueOf(e.getStatusCode().value()) : HttpStatus.INTERNAL_SERVER_ERROR;
        return ResponseEntity.status(status)
                .body(ApiResponse.error(status.value(), e.getReason() == null ? status.getReasonPhrase() : e.getReason()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ApiResponse<Void>> handleBadRequest(IllegalArgumentException e) {
        return ResponseEntity.badRequest().body(ApiResponse.error(400, e.getMessage()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<Void>> handleUnexpected(Exception e) {
        return ResponseEntity.internalServerError().body(ApiResponse.error(500, e.getMessage()));
    }
}
