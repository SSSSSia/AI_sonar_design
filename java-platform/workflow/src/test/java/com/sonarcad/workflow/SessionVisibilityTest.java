package com.sonarcad.workflow;

import com.sonarcad.workflow.session.DesignSessionEntity;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.session.ProjectVisibilityMapper;
import com.sonarcad.workflow.session.SessionService;
import com.sonarcad.workflow.mq.TaskPublisher;
import com.sonarcad.workflow.notify.NotifyClient;
import com.sonarcad.workflow.session.CadTuneLookupMapper;
import com.sonarcad.workflow.session.KbBaseLookupMapper;
import com.sonarcad.workflow.session.ProjectLookupMapper;
import com.sonarcad.workflow.session.UserLookupMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * T13.4 会话可见性断言单测：assertVisible（admin 直通由控制器保证；此处验证
 * owner/成员通过、无关联 404 不暴露存在性）。纯 Mockito（无 DB）。
 */
class SessionVisibilityTest {

    private SessionService service;
    private ProjectVisibilityMapper visibilityMapper;
    private DesignSessionEntity session;

    @BeforeEach
    void setUp() {
        visibilityMapper = mock(ProjectVisibilityMapper.class);
        service = new SessionService(mock(com.sonarcad.workflow.session.DesignSessionMapper.class),
                mock(IrSnapshotMapper.class), mock(TaskPublisher.class),
                new NotifyClient("http://127.0.0.1:1"), mock(UserLookupMapper.class),
                mock(ProjectLookupMapper.class), mock(KbBaseLookupMapper.class),
                mock(CadTuneLookupMapper.class), visibilityMapper);
        session = new DesignSessionEntity();
        session.setId("s-1");
        session.setProjectId("p-1");
    }

    @Test
    void memberProjectPasses() {
        when(visibilityMapper.canSeeProject("p-1", "u-member")).thenReturn(true);
        assertThatCode(() -> service.assertVisible(session, "u-member")).doesNotThrowAnyException();
    }

    @Test
    void unrelatedUserGets404() {
        when(visibilityMapper.canSeeProject("p-1", "u-outsider")).thenReturn(false);
        assertThatThrownBy(() -> service.assertVisible(session, "u-outsider"))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode().value()).isEqualTo(404));
    }

    @Test
    void blankUserIdGets404() {
        assertThatThrownBy(() -> service.assertVisible(session, ""))
                .isInstanceOf(ResponseStatusException.class);
    }
}
