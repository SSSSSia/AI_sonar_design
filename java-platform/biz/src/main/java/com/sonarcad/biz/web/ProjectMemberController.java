package com.sonarcad.biz.web;

import com.sonarcad.biz.audit.AuditRecorder;
import com.sonarcad.biz.member.ProjectMemberMapper;
import com.sonarcad.biz.member.ProjectVisibility;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 项目成员管理 API（T13.4，§9.4 项目级粒度 + §5.7 成员分配）：
 *   GET    /api/v1/projects/{id}/members            成员列表（可见者均可读）
 *   POST   /api/v1/projects/{id}/members            添加成员（admin/owner；按 username 解析）
 *   DELETE /api/v1/projects/{id}/members/{userId}   移除成员（admin/owner；owner 行不可移除）
 * 建表时已回填 projects.owner_id → role=owner（V18）；不提供 owner 转移（留后续）。
 */
@RestController
@RequestMapping("/api/v1/projects/{id}/members")
public class ProjectMemberController {

    private final ProjectMemberMapper memberMapper;
    private final ProjectVisibility visibility;
    private final AuditRecorder auditRecorder;

    public ProjectMemberController(ProjectMemberMapper memberMapper, ProjectVisibility visibility,
                                   AuditRecorder auditRecorder) {
        this.memberMapper = memberMapper;
        this.visibility = visibility;
        this.auditRecorder = auditRecorder;
    }

    public record AddMemberRequest(String username, String role) {}

    @GetMapping
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ProjectController.ApiResponse<List<Map<String, Object>>> list(@PathVariable String id,
                                                       @RequestHeader(value = "X-User-Id", required = false) String userId,
                                                       @RequestHeader(value = "X-User-Roles", required = false) String roles) {
        requireVisible(id, userId, roles);
        return ProjectController.ApiResponse.ok(memberMapper.listForProject(id));
    }

    @PostMapping
    @PreAuthorize("hasAnyRole('admin','designer')")
    public ProjectController.ApiResponse<Map<String, Object>> add(@PathVariable String id,
                                                @RequestBody AddMemberRequest req,
                                                @RequestHeader(value = "X-User-Id", required = false) String userId,
                                                @RequestHeader(value = "X-User-Roles", required = false) String roles) {
        if (!visibility.canManage(id, userId, roles)) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.FORBIDDEN, "仅 admin 或项目 owner 可管理成员");
        }
        if (req == null || req.username() == null || req.username().isBlank()) {
            throw new IllegalArgumentException("username 不能为空");
        }
        String targetUserId = memberMapper.findUserIdByUsername(req.username().trim());
        if (targetUserId == null) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.NOT_FOUND, "用户不存在: " + req.username());
        }
        memberMapper.upsert(id, targetUserId, "member");
        auditRecorder.record(userId, "project.add_member", "project", id,
                Map.of("member", req.username().trim()), null);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("project_id", id);
        out.put("user_id", targetUserId);
        out.put("username", req.username().trim());
        out.put("role", "member");
        return ProjectController.ApiResponse.ok(out);
    }

    @DeleteMapping("/{userId}")
    @PreAuthorize("hasAnyRole('admin','designer')")
    public ProjectController.ApiResponse<Map<String, Object>> remove(@PathVariable String id, @PathVariable String userId,
                                                   @RequestHeader(value = "X-User-Id", required = false) String operatorId,
                                                   @RequestHeader(value = "X-User-Roles", required = false) String roles) {
        if (!visibility.canManage(id, operatorId, roles)) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.FORBIDDEN, "仅 admin 或项目 owner 可管理成员");
        }
        int deleted = memberMapper.deleteMember(id, userId);
        if (deleted == 0) {
            throw new IllegalArgumentException("成员不存在或为项目 owner（owner 不可移除，仅可随项目删除）");
        }
        auditRecorder.record(operatorId, "project.remove_member", "project", id,
                Map.of("member_id", userId), null);
        return ProjectController.ApiResponse.ok(Map.of("project_id", id, "user_id", userId, "removed", true));
    }

    private void requireVisible(String id, String userId, String roles) {
        if (!visibility.canSee(id, userId, roles)) {
            throw new org.springframework.web.server.ResponseStatusException(
                    org.springframework.http.HttpStatus.NOT_FOUND, "项目不存在: " + id);
        }
    }
}
