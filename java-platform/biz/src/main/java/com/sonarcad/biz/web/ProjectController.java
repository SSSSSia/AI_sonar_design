package com.sonarcad.biz.web;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.biz.project.ProjectEntity;
import com.sonarcad.biz.project.ProjectMapper;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.UUID;

/**
 * 项目最小 API（PHASE-001 §2，T1.1 仅支撑会话创建所需的最小 CRUD；完整 §5.7 能力随阶段 2/3 补全）：
 *   POST /api/v1/projects          创建
 *   GET  /api/v1/projects?page=    分页
 *   GET  /api/v1/projects/{id}
 */
@RestController
@RequestMapping("/api/v1/projects")
public class ProjectController {

    private final ProjectMapper projectMapper;

    public ProjectController(ProjectMapper projectMapper) {
        this.projectMapper = projectMapper;
    }

    public record CreateProjectRequest(String name, String code, String description) {}

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
    public ApiResponse<Page<ProjectEntity>> list(@RequestParam(defaultValue = "1") long page,
                                                 @RequestParam(defaultValue = "20") long size,
                                                 @RequestParam(required = false) String keyword) {
        return ApiResponse.ok(projectMapper.selectPage(new Page<>(page, Math.min(size, 100)),
                new LambdaQueryWrapper<ProjectEntity>()
                        .like(keyword != null && !keyword.isBlank(), ProjectEntity::getName, keyword)
                        .orderByDesc(ProjectEntity::getCreatedAt)));
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<ProjectEntity> get(@PathVariable String id) {
        ProjectEntity p = projectMapper.selectById(id);
        if (p == null) throw new IllegalArgumentException("项目不存在: " + id);
        return ApiResponse.ok(p);
    }

    /** 统一响应包（宪法 §5.3） */
    public record ApiResponse<T>(int code, String message, T data, String traceId) {
        public static <T> ApiResponse<T> ok(T data) {
            return new ApiResponse<>(0, "ok", data, UUID.randomUUID().toString());
        }
    }
}
