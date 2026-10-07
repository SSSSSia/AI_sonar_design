package com.sonarcad.biz.member;

import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 项目可见性/管理权判定（T13.4，§9.4「项目级」粒度 + 附录 C「本人项目」）：
 * admin 全量；owner/成员可见（memberRole 非 null）；管理权（增删成员）= admin 或 owner。
 * userId/roles 来自 gateway 注入的信任头（X-User-Id / X-User-Roles）。
 */
@Component
public class ProjectVisibility {

    private final ProjectMemberMapper mapper;

    public ProjectVisibility(ProjectMemberMapper mapper) {
        this.mapper = mapper;
    }

    public boolean canSee(String projectId, String userId, String rolesHeader) {
        if (isAdmin(rolesHeader)) {
            return true;
        }
        if (userId == null || userId.isBlank()) {
            return false;
        }
        return mapper.memberRole(projectId, userId) != null;
    }

    public boolean canManage(String projectId, String userId, String rolesHeader) {
        if (isAdmin(rolesHeader)) {
            return true;
        }
        if (userId == null || userId.isBlank()) {
            return false;
        }
        return "owner".equals(mapper.memberRole(projectId, userId));
    }

    public static boolean isAdmin(String rolesHeader) {
        if (rolesHeader == null) {
            return false;
        }
        for (String r : rolesHeader.split(",")) {
            if ("admin".equals(r.trim())) {
                return true;
            }
        }
        return false;
    }

    public static List<String> roles(String rolesHeader) {
        if (rolesHeader == null || rolesHeader.isBlank()) {
            return List.of();
        }
        return List.of(rolesHeader.split(",")).stream().map(String::trim).toList();
    }
}
