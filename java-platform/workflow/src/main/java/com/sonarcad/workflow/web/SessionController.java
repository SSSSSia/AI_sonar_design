package com.sonarcad.workflow.web;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.workflow.session.DesignSessionEntity;
import com.sonarcad.workflow.session.SessionService;
import com.sonarcad.workflow.statemachine.PipelineEvent;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 会话端点（PHASE-001 §3.1，宪法 §5.4 基线）：
 *   POST /api/v1/workflow/sessions                     创建（pending）
 *   GET  /api/v1/workflow/sessions?projectId=&page=     分页
 *   GET  /api/v1/workflow/sessions/{id}
 *   GET  /api/v1/workflow/sessions/{id}/allowed-events  可触发事件自省
 *   POST /api/v1/workflow/sessions/{id}/events          事件驱动转移（非法 409）
 */
@RestController
@RequestMapping("/api/v1/workflow/sessions")
public class SessionController {

    private final SessionService sessionService;

    public SessionController(SessionService sessionService) {
        this.sessionService = sessionService;
    }

    public record CreateSessionRequest(String projectId, String title) {}

    @PostMapping
    @PreAuthorize("hasAnyRole('admin','designer')")
    public ApiResponse<DesignSessionEntity> create(@RequestBody CreateSessionRequest req,
                                                   @RequestHeader(value = "X-User-Id", required = false) String userId) {
        if (req.projectId() == null || req.projectId().isBlank()) {
            throw new IllegalArgumentException("projectId 不能为空");
        }
        return ApiResponse.ok(sessionService.create(req.projectId(), req.title(), userId));
    }

    @GetMapping
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<Page<DesignSessionEntity>> list(
            @RequestParam(required = false) String projectId,
            @RequestParam(defaultValue = "1") long page,
            @RequestParam(defaultValue = "20") long size) {
        return ApiResponse.ok(sessionService.list(projectId, page, Math.min(size, 100)));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<DesignSessionEntity> get(@PathVariable String id) {
        return ApiResponse.ok(sessionService.get(id));
    }

    @GetMapping("/{id}/allowed-events")
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<Map<String, Object>> allowedEvents(@PathVariable String id) {
        return ApiResponse.ok(sessionService.allowedEvents(id));
    }

    public record FireEventRequest(String event) {}

    @PostMapping("/{id}/events")
    @PreAuthorize("hasAnyRole('admin','designer')")
    public ApiResponse<DesignSessionEntity> fireEvent(@PathVariable String id, @RequestBody FireEventRequest req) {
        PipelineEvent event;
        try {
            event = PipelineEvent.valueOf(req.event());
        } catch (Exception e) {
            throw new IllegalArgumentException("未知事件: " + req.event());
        }
        return ApiResponse.ok(sessionService.fireEvent(id, event));
    }
}
