package com.sonarcad.biz;

import com.sonarcad.biz.audit.AuditRecorder;
import com.sonarcad.biz.member.ProjectMemberMapper;
import com.sonarcad.biz.member.ProjectVisibility;
import com.sonarcad.biz.web.ProjectController;
import com.sonarcad.biz.web.ProjectMemberController;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * T13.4 项目成员管理单测：管理权门禁（admin/owner，其余 403）、username 解析、
 * owner 行不可移除。纯 Mockito 单测（无 DB）；SQL 正确性由容器栈实机验证承接。
 */
class ProjectMemberControllerTest {

    private ProjectMemberMapper memberMapper;
    private ProjectMemberController controller;

    private static final String PID = "p-1";
    private static final String OWNER_ID = "u-owner";
    private static final String MEMBER_ID = "u-member";

    @BeforeEach
    void setUp() {
        memberMapper = mock(ProjectMemberMapper.class);
        AuditRecorder recorder = mock(AuditRecorder.class);
        controller = new ProjectMemberController(memberMapper, new ProjectVisibility(memberMapper), recorder);
    }

    @Test
    void ownerAddsMemberByUsername() {
        when(memberMapper.memberRole(PID, OWNER_ID)).thenReturn("owner");
        when(memberMapper.findUserIdByUsername("bob")).thenReturn("u-bob");

        var out = controller.add(PID, new ProjectMemberController.AddMemberRequest("bob", null),
                OWNER_ID, "designer");

        assertThat(out.data()).containsEntry("username", "bob").containsEntry("role", "member");
        verify(memberMapper).upsert(PID, "u-bob", "member");
    }

    @Test
    void nonOwnerMemberCannotManage() {
        when(memberMapper.memberRole(PID, MEMBER_ID)).thenReturn("member");
        assertThatThrownBy(() -> controller.add(PID,
                new ProjectMemberController.AddMemberRequest("bob", null), MEMBER_ID, "designer"))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(403));
    }

    @Test
    void unknownUserIs404() {
        when(memberMapper.memberRole(PID, OWNER_ID)).thenReturn("owner");
        when(memberMapper.findUserIdByUsername("ghost")).thenReturn(null);
        assertThatThrownBy(() -> controller.add(PID,
                new ProjectMemberController.AddMemberRequest("ghost", null), OWNER_ID, "admin"))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(404));
    }

    @Test
    void ownerRowCannotBeRemoved() {
        when(memberMapper.memberRole(PID, OWNER_ID)).thenReturn("owner");
        when(memberMapper.deleteMember(PID, "u-owner-row")).thenReturn(0);
        assertThatThrownBy(() -> controller.remove(PID, "u-owner-row", OWNER_ID, "admin"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("owner");
    }

    @Test
    void invisibleProjectListIs404() {
        when(memberMapper.memberRole(PID, "u-outsider")).thenReturn(null);
        assertThatThrownBy(() -> controller.list(PID, "u-outsider", "viewer"))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(404));
    }

    @Test
    void adminBypassesVisibility() {
        when(memberMapper.listForProject(PID)).thenReturn(List.of(Map.of("user_id", MEMBER_ID)));
        ProjectController.ApiResponse<List<Map<String, Object>>> out = controller.list(PID, "u-admin", "admin");
        assertThat(out.data()).hasSize(1);
    }
}
