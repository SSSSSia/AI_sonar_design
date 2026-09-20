package com.sonarcad.biz.web;

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
    private final ProjectController controller = new ProjectController(mapper);

    private static ProjectEntity project(String id, String name) {
        ProjectEntity p = new ProjectEntity();
        p.setId(id);
        p.setName(name);
        return p;
    }

    @Test
    void missingProjectIs404() {
        when(mapper.selectById("p1")).thenReturn(null);
        assertThatThrownBy(() -> controller.delete("p1"))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(404));
    }

    @Test
    void projectWithSessionsIs409() {
        when(mapper.selectById("p1")).thenReturn(project("p1", "有会话项目"));
        when(mapper.countSessions("p1")).thenReturn(3L);
        assertThatThrownBy(() -> controller.delete("p1"))
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
        Map<String, Object> data = controller.delete("p1").data();
        assertThat(data).containsEntry("deleted", true).containsEntry("id", "p1");
        verify(mapper).deleteById("p1");
    }

    @Test
    void patchRenamesProject() {
        ProjectEntity p = project("p1", "旧名");
        when(mapper.selectById("p1")).thenReturn(p);
        ProjectEntity out = controller.patch("p1",
                new ProjectController.PatchProjectRequest("新名", "TLA-2", null)).data();
        assertThat(out.getName()).isEqualTo("新名");
        assertThat(out.getCode()).isEqualTo("TLA-2");
        verify(mapper).updateById(p);
    }

    @Test
    void patchMissingProjectIs404() {
        when(mapper.selectById("p1")).thenReturn(null);
        assertThatThrownBy(() -> controller.patch("p1",
                new ProjectController.PatchProjectRequest("新名", null, null)))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(404));
    }

    @Test
    void patchRejectsBlankNameAndEmptyBody() {
        when(mapper.selectById("p1")).thenReturn(project("p1", "旧名"));
        assertThatThrownBy(() -> controller.patch("p1",
                new ProjectController.PatchProjectRequest(" ", null, null)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("name");
        assertThatThrownBy(() -> controller.patch("p1",
                new ProjectController.PatchProjectRequest(null, null, null)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("至少");
        verify(mapper, never()).updateById(any(ProjectEntity.class));
    }
}
