package com.sonarcad.biz.web;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.biz.audit.AuditRecorder;
import com.sonarcad.biz.project.ProjectEntity;
import com.sonarcad.biz.project.ProjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 项目最小 API（PHASE-001 §2，T1.1 仅支撑会话创建所需的最小 CRUD；完整 §5.7 能力随阶段 2/3 补全）：
 *   POST   /api/v1/projects          创建
 *   GET    /api/v1/projects?page=    分页（附带 session_count，2026-09-20 项目管理用）
 *   GET    /api/v1/projects/{id}
 *   DELETE /api/v1/projects/{id}     删除（admin；项目下仍有会话则 409 拒绝——先删会话）
 * T13.1：创建/修改/删除落审计（§9.4；成员管理随 T13.4）。
 */
@RestController
@RequestMapping("/api/v1/projects")
public class ProjectController {

    private final ProjectMapper projectMapper;
    private final AuditRecorder auditRecorder;

    public ProjectController(ProjectMapper projectMapper, AuditRecorder auditRecorder) {
        this.projectMapper = projectMapper;
        this.auditRecorder = auditRecorder;
    }

    public record CreateProjectRequest(String name, String code, String description) {}

    public record PatchProjectRequest(String name, String code, String description) {}

    @PatchMapping("/{id}")
    @PreAuthorize("hasAnyRole('admin','designer')")
    public ApiResponse<ProjectEntity> patch(@PathVariable String id, @RequestBody PatchProjectRequest req,
                                            @RequestHeader(value = "X-User-Id", required = false) String userId) {
        ProjectEntity p = projectMapper.selectById(id);
        if (p == null) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "项目不存在: " + id);
        }
        if (req.name() == null && req.code() == null && req.description() == null) {
            throw new IllegalArgumentException("至少提供 name / code / description 中的一个字段");
        }
        if (req.name() != null) {
            if (req.name().isBlank()) throw new IllegalArgumentException("name 不能为空");
            p.setName(req.name());
        }
        if (req.code() != null) p.setCode(req.code());
        if (req.description() != null) p.setDescription(req.description());
        p.setUpdatedAt(java.time.OffsetDateTime.now());
        projectMapper.updateById(p);
        auditRecorder.record(userId, "project.patch", "project", p.getId(),
                Map.of("name", p.getName()), null);
        return ApiResponse.ok(p);
    }

    @PostMapping
    @PreAuthorize("hasAnyRole('admin','designer')")
    public ApiResponse<ProjectEntity> create(@RequestBody CreateProjectRequest req,
                                             @RequestHeader(value = "X-User-Id", required = false) String userId) {
        if (req.name() == null || req.name().isBlank()) {
            throw new IllegalArgumentException("name 不能为空");
        }
        ProjectEntity p = new ProjectEntity();
        p.setName(req.name());
        p.setCode(req.code());
        p.setDescription(req.description());
        p.setOwnerId(userId);
        p.setStatus("active");
        projectMapper.insert(p);
        auditRecorder.record(userId, "project.create", "project", p.getId(),
                Map.of("name", p.getName()), null);
        return ApiResponse.ok(p);
    }

    @GetMapping
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<IPage<Map<String, Object>>> list(@RequestParam(defaultValue = "1") long page,
                                                       @RequestParam(defaultValue = "20") long size,
                                                       @RequestParam(required = false) String keyword) {
        Page<ProjectEntity> result = projectMapper.selectPage(new Page<>(page, Math.min(size, 100)),
                new LambdaQueryWrapper<ProjectEntity>()
                        .like(keyword != null && !keyword.isBlank(), ProjectEntity::getName, keyword)
                        .orderByDesc(ProjectEntity::getCreatedAt));
        // 状态分布（含总数）与最近活动：一次分组查询取齐，避免逐项目 N+1
        Map<String, Map<String, Long>> statsByProject = new java.util.HashMap<>();
        for (Map<String, Object> row : projectMapper.countSessionsByStatus()) {
            String pid = String.valueOf(row.get("project_id"));
            long cnt = ((Number) row.get("cnt")).longValue();
            String status = String.valueOf(row.get("status"));
            statsByProject.computeIfAbsent(pid, k -> new java.util.HashMap<>()).merge(status, cnt, Long::sum);
        }
        Map<String, Object> lastByProject = new java.util.HashMap<>();
        for (Map<String, Object> row : projectMapper.lastSessionActivity()) {
            lastByProject.put(String.valueOf(row.get("project_id")), row.get("last_at"));
        }
        return ApiResponse.ok(result.convert(p -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", p.getId());
            row.put("name", p.getName());
            row.put("code", p.getCode());
            row.put("description", p.getDescription());
            row.put("owner_id", p.getOwnerId());
            row.put("status", p.getStatus());
            row.put("created_at", p.getCreatedAt());
            Map<String, Long> stats = statsByProject.getOrDefault(p.getId(), Map.of());
            row.put("session_count", stats.values().stream().mapToLong(Long::longValue).sum());
            Map<String, Long> statDetail = new LinkedHashMap<>();
            for (String s : java.util.List.of("pending", "running", "paused", "completed", "failed")) {
                statDetail.put(s, stats.getOrDefault(s, 0L));
            }
            row.put("session_stats", statDetail);
            row.put("last_active_at", lastByProject.get(p.getId()));
            return row;
        }));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<ProjectEntity> get(@PathVariable String id) {
        ProjectEntity p = projectMapper.selectById(id);
        if (p == null) throw new IllegalArgumentException("项目不存在: " + id);
        return ApiResponse.ok(p);
    }

    @DeleteMapping("/{id}")
    @PreAuthorize("hasRole('admin')")
    public ApiResponse<Map<String, Object>> delete(@PathVariable String id,
                                                   @RequestHeader(value = "X-User-Id", required = false) String userId) {
        ProjectEntity p = projectMapper.selectById(id);
        if (p == null) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "项目不存在: " + id);
        }
        long sessions = projectMapper.countSessions(id);
        if (sessions > 0) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "项目「" + p.getName() + "」下仍有 " + sessions + " 个会话，请先在仪表盘删除这些会话");
        }
        projectMapper.deleteById(id);
        auditRecorder.record(userId, "project.delete", "project", id,
                Map.of("name", p.getName()), null);
        return ApiResponse.ok(Map.of("id", id, "deleted", true, "name", p.getName()));
    }

    /** 统一响应包（宪法 §5.3） */
    public record ApiResponse<T>(int code, String message, T data, String traceId) {
        public static <T> ApiResponse<T> ok(T data) {
            return new ApiResponse<>(0, "ok", data, UUID.randomUUID().toString());
        }
    }
}
