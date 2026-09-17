package com.sonarcad.auth.web;

import java.util.UUID;

/** 统一响应包（宪法 §5.3）：{ code, message, data, trace_id } */
public record ApiResponse<T>(int code, String message, T data, String traceId) {

    public static <T> ApiResponse<T> ok(T data) {
        return new ApiResponse<>(0, "ok", data, UUID.randomUUID().toString());
    }

    public static <T> ApiResponse<T> error(int code, String message) {
        return new ApiResponse<>(code, message, null, UUID.randomUUID().toString());
    }
}
