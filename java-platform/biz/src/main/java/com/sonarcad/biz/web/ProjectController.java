package com.sonarcad.biz.web;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.biz.project.ProjectEntity;
import com.sonarcad.biz.project.ProjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * 项目最小 API（PHASE-001 §2，T1.1 仅支撑会话创建所需的最小 CRUD；完整 §5.7 能力随阶段 2/3 补全）：
 *   POST   /api/v1/projects          创建
 *   GET    /api/v1/projects?page=    分页（附带 session_count，2026-09-20 项目管理用）
 *   GET    /api/v1/projects/{id}
 *   DELETE /api/v1/projects/{id}     删除（admin；项目下仍有会话则 409 拒绝——先删会话）
 */
@RestController
@RequestMapping("/api/v1/projects")
public class ProjectController {

    private final ProjectMapper projectMapper;

    public ProjectController(ProjectMapper projectMapper) {
        this.projectMapper = projectMapper;
    }

    public record CreateProjectRequest(String name, String code, String description) {}

    public record PatchProjectRequest(String name, String code, String description) {}

    @PatchMapping("/{id}")
    @PreAuthorize("hasAnyRole('admin','designer')")
    public ApiResponse<ProjectEntity> patch(@PathVariable String id, @RequestBody PatchProjectRequest req) {
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
        Map<String, Long> counts = projectMapper.countSessionsGrouped().stream()
                .collect(Collectors.toMap(m -> String.valueOf(m.get("project_id")),
                        m -> ((Number) m.get("cnt")).longValue()));
        return ApiResponse.ok(result.convert(p -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", p.getId());
            row.put("name", p.getName());
            row.put("code", p.getCode());
            row.put("description", p.getDescription());
            row.put("owner_id", p.getOwnerId());
            row.put("status", p.getStatus());
            row.put("created_at", p.getCreatedAt());
            row.put("session_count", counts.getOrDefault(p.getId(), 0L));
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
    public ApiResponse<Map<String, Object>> delete(@PathVariable String id) {
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
        return ApiResponse.ok(Map.of("id", id, "deleted", true, "name", p.getName()));
    }

    /** 统一响应包（宪法 §5.3） */
    public record ApiResponse<T>(int code, String message, T data, String traceId) {
        public static <T> ApiResponse<T> ok(T data) {
            return new ApiResponse<>(0, "ok", data, UUID.randomUUID().toString());
        }
    }
}
