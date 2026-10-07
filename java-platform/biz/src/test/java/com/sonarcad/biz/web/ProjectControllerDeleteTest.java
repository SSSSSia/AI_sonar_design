package com.sonarcad.biz.web;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;

import com.sonarcad.biz.project.ProjectEntity;
import com.sonarcad.biz.project.ProjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 项目删除保护（2026-09-20）：不存在 404、有会话 409 拒删、零会话放行。 */
class ProjectControllerDeleteTest {

    private final ProjectMapper mapper = mock(ProjectMapper.class);
    private final com.sonarcad.biz.audit.AuditRecorder auditRecorder = mock(com.sonarcad.biz.audit.AuditRecorder.class);
    private final ProjectController controller = new ProjectController(mapper, auditRecorder);

    private static ProjectEntity project(String id, String name) {
        ProjectEntity p = new ProjectEntity();
        p.setId(id);
        p.setName(name);
        return p;
    }

    @Test
    void missingProjectIs404() {
        when(mapper.selectById("p1")).thenReturn(null);
        assertThatThrownBy(() -> controller.delete("p1", null))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(404));
    }

    @Test
    void projectWithSessionsIs409() {
        when(mapper.selectById("p1")).thenReturn(project("p1", "有会话项目"));
        when(mapper.countSessions("p1")).thenReturn(3L);
        assertThatThrownBy(() -> controller.delete("p1", null))
                .isInstanceOfSatisfying(ResponseStatusException.class, e -> {
                    assertThat(e.getStatusCode().value()).isEqualTo(409);
                    assertThat(e.getReason()).contains("3").contains("会话");
                });
        verify(mapper, never()).deleteById(anyString());
    }

    @Test
    void emptyProjectDeletes() {
        when(mapper.selectById("p1")).thenReturn(project("p1", "空项目"));
        when(mapper.countSessions("p1")).thenReturn(0L);
        Map<String, Object> data = controller.delete("p1", null).data();
        assertThat(data).containsEntry("deleted", true).containsEntry("id", "p1");
        verify(mapper).deleteById("p1");
    }

    @Test
    void patchRenamesProject() {
        ProjectEntity p = project("p1", "旧名");
        when(mapper.selectById("p1")).thenReturn(p);
        ProjectEntity out = controller.patch("p1",
                new ProjectController.PatchProjectRequest("新名", "TLA-2", null), null).data();
        assertThat(out.getName()).isEqualTo("新名");
        assertThat(out.getCode()).isEqualTo("TLA-2");
        verify(mapper).updateById(p);
    }

    @Test
    void patchMissingProjectIs404() {
        when(mapper.selectById("p1")).thenReturn(null);
        assertThatThrownBy(() -> controller.patch("p1",
                new ProjectController.PatchProjectRequest("新名", null, null), null))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(404));
    }

    @Test
    void patchRejectsBlankNameAndEmptyBody() {
        when(mapper.selectById("p1")).thenReturn(project("p1", "旧名"));
        assertThatThrownBy(() -> controller.patch("p1",
                new ProjectController.PatchProjectRequest(" ", null, null), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("name");
        assertThatThrownBy(() -> controller.patch("p1",
                new ProjectController.PatchProjectRequest(null, null, null), null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("至少");
        verify(mapper, never()).updateById(any(ProjectEntity.class));
    }

    @Test
    void listMergesSessionStatsAndLastActive() {
        ProjectEntity p1 = project("p1", "有会话项目");
        Page<ProjectEntity> page = new Page<>(1, 10);
        page.setRecords(java.util.List.of(p1));
        when(mapper.selectPage(any(), any())).thenReturn(page);
        // p1: 1 running + 2 completed；p2（不在列表）不应污染
        when(mapper.countSessionsByStatus()).thenReturn(java.util.List.of(
                Map.of("project_id", "p1", "status", "running", "cnt", 1L),
                Map.of("project_id", "p1", "status", "completed", "cnt", 2L),
                Map.of("project_id", "p2", "status", "running", "cnt", 5L)));
        java.time.OffsetDateTime last = java.time.OffsetDateTime.now();
        when(mapper.lastSessionActivity()).thenReturn(java.util.List.of(
                Map.of("project_id", "p1", "last_at", last)));

        var row = controller.list(1, 20, null).data().getRecords().get(0);
        assertThat(row.get("session_count")).isEqualTo(3L);
        @SuppressWarnings("unchecked")
        Map<String, Long> stats = (Map<String, Long>) row.get("session_stats");
        assertThat(stats).containsEntry("running", 1L).containsEntry("completed", 2L)
                .containsEntry("failed", 0L).containsEntry("paused", 0L);
        assertThat(row.get("last_active_at")).isEqualTo(last);
    }
}
