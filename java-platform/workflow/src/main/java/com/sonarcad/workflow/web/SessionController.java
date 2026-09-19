package com.sonarcad.workflow.web;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.workflow.session.DesignSessionEntity;
import com.sonarcad.workflow.session.SessionCleanupService;
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
    private final SessionCleanupService cleanupService;

    public SessionController(SessionService sessionService, SessionCleanupService cleanupService) {
        this.sessionService = sessionService;
        this.cleanupService = cleanupService;
    }

    public record CreateSessionRequest(String projectId, String title, String requirement) {}

    @PostMapping
    @PreAuthorize("hasAnyRole('admin','designer')")
    public ApiResponse<DesignSessionEntity> create(@RequestBody CreateSessionRequest req,
                                                   @RequestHeader(value = "X-User-Id", required = false) String userId) {
        if (req.projectId() == null || req.projectId().isBlank()) {
            throw new IllegalArgumentException("projectId 不能为空");
        }
        return ApiResponse.ok(sessionService.create(req.projectId(), req.title(), req.requirement(), userId));
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

    /** 删除会话（admin）：终态直接删；运行中/暂停须 force（会话删除级联说明见 SessionCleanupService）。 */
    @DeleteMapping("/{id}")
    @PreAuthorize("hasRole('admin')")
    public ApiResponse<Map<String, Object>> delete(@PathVariable String id,
                                                   @RequestParam(defaultValue = "false") boolean force) {
        return ApiResponse.ok(cleanupService.delete(id, force));
    }
}
