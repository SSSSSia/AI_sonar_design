package com.sonarcad.biz.web;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 统一错误响应（宪法 §5.3）。 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ProjectController.ApiResponse<Void>> handleBadRequest(IllegalArgumentException e) {
        return ResponseEntity.badRequest()
                .body(new ProjectController.ApiResponse<>(400, e.getMessage(), null, java.util.UUID.randomUUID().toString()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ProjectController.ApiResponse<Void>> handleUnexpected(Exception e) {
        return ResponseEntity.internalServerError()
                .body(new ProjectController.ApiResponse<>(500, e.getMessage(), null, java.util.UUID.randomUUID().toString()));
    }
}
