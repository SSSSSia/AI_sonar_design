package com.sonarcad.biz.web;

import com.sonarcad.biz.project.ProjectEntity;
import com.sonarcad.biz.project.ProjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
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
}
