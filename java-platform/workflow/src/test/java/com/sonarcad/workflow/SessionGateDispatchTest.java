package com.sonarcad.workflow;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.workflow.mq.TaskPublisher;
import com.sonarcad.workflow.notify.NotifyClient;
import com.sonarcad.workflow.session.DesignSessionEntity;
import com.sonarcad.workflow.session.DesignSessionMapper;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.session.ProjectLookupMapper;
import com.sonarcad.workflow.session.SessionService;
import com.sonarcad.workflow.session.UserLookupMapper;
import com.sonarcad.workflow.statemachine.PipelineEvent;
import com.sonarcad.workflow.statemachine.PipelineStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.web.server.ResponseStatusException;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/**
 * T2.2 审阅关接线单测：STAGE_DONE 分支按 current_stage 续派或暂停（mock 持久层，不起 Spring 容器）。
 */
class SessionGateDispatchTest {

    private DesignSessionMapper sessionMapper;
    private IrSnapshotMapper irSnapshotMapper;
    private TaskPublisher taskPublisher;
    private ProjectLookupMapper projectLookupMapper;
    private SessionService service;

    @BeforeEach
    void setUp() {
        sessionMapper = mock(DesignSessionMapper.class);
        irSnapshotMapper = mock(IrSnapshotMapper.class);
        taskPublisher = mock(TaskPublisher.class);
        taskPublisher = mock(TaskPublisher.class);
        projectLookupMapper = mock(ProjectLookupMapper.class);
        // 真实 NotifyClient：发布为异步 fire-and-forget，测试中连不上只记日志、不影响断言
        service = new SessionService(sessionMapper, irSnapshotMapper, taskPublisher,
                new NotifyClient("http://127.0.0.1:1"), mock(UserLookupMapper.class),
                projectLookupMapper);
    }

    @Test
    void listFillsProjectNames() {
        // 纯 Mockito 不跑 MyBatis 启动，lambda 列缓存需手动注册（fillProjectNames 用 LambdaQueryWrapper）
        com.baomidou.mybatisplus.core.metadata.TableInfoHelper.initTableInfo(
                new org.apache.ibatis.builder.MapperBuilderAssistant(
                        new com.baomidou.mybatisplus.core.MybatisConfiguration(), ""),
                com.sonarcad.workflow.session.ProjectLookupEntity.class);
        DesignSessionEntity s = session("s1", "pending", null);
        s.setProjectId("p-1");
        Page<DesignSessionEntity> page = new Page<>(1, 10);
        page.setRecords(java.util.List.of(s));
        when(sessionMapper.selectPage(any(), any())).thenReturn(page);
        com.sonarcad.workflow.session.ProjectLookupEntity pe =
                new com.sonarcad.workflow.session.ProjectLookupEntity();
        pe.setId("p-1");
        pe.setName("人工测试");
        when(projectLookupMapper.selectList(any())).thenReturn(java.util.List.of(pe));

        Page<DesignSessionEntity> out = service.list(null, 1, 10);
        assertEquals("人工测试", out.getRecords().get(0).getProjectName());
    }

    private DesignSessionEntity session(String id, String status, String stage) {
        DesignSessionEntity s = new DesignSessionEntity();
        s.setId(id);
        s.setPipelineStatus(status);
        s.setCurrentStage(stage);
        return s;
    }

    @Test
    void stageDoneAfterA2PausesGate1() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A2"));
        when(sessionMapper.update(any(), any())).thenReturn(1);
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A2"));

        service.fireEvent("s1", PipelineEvent.STAGE_DONE);

        ArgumentCaptor<DesignSessionEntity> patch = ArgumentCaptor.forClass(DesignSessionEntity.class);
        verify(sessionMapper).update(patch.capture(), any());
        assertEquals(PipelineStatus.paused.name(), patch.getValue().getPipelineStatus());
        assertEquals("gate1", patch.getValue().getCurrentStage());
        verify(taskPublisher, never()).publishA3Task(any());
    }

    @Test
    void stageDoneAfterA4PausesGate2() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A4"));
        when(sessionMapper.update(any(), any())).thenReturn(1);

        service.fireEvent("s1", PipelineEvent.STAGE_DONE);

        ArgumentCaptor<DesignSessionEntity> patch = ArgumentCaptor.forClass(DesignSessionEntity.class);
        verify(sessionMapper).update(patch.capture(), any());
        assertEquals(PipelineStatus.paused.name(), patch.getValue().getPipelineStatus());
        assertEquals("gate2", patch.getValue().getCurrentStage());
    }

    @Test
    void stageDoneAfterA3DispatchesA4() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A3"));

        service.fireEvent("s1", PipelineEvent.STAGE_DONE);

        verify(taskPublisher).publishA4Task("s1");
        verify(sessionMapper, never()).update(any(), any());
    }

    @Test
    void stageDoneAfterA1DispatchesA2() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A1"));

        service.fireEvent("s1", PipelineEvent.STAGE_DONE);

        verify(taskPublisher).publishA2Task("s1");
    }

    @Test
    void pauseConflictSurfaces409() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "completed", "A2"));
        when(sessionMapper.update(any(), any())).thenReturn(0);

        assertThrows(ResponseStatusException.class,
                () -> service.fireEvent("s1", PipelineEvent.STAGE_DONE));
        verify(taskPublisher, never()).publishA3Task(any());
    }

    @Test
    void startMarksStageA1Immediately() {
        // 启动空窗修复（2026-09-19 用户反馈）：START 即落 current_stage=A1，不等 worker 回写
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "pending", null));
        when(sessionMapper.update(any(), any())).thenReturn(1);

        DesignSessionEntity out = service.fireEvent("s1", PipelineEvent.START);

        assertEquals(PipelineStatus.running.name(), out.getPipelineStatus());
        assertEquals("A1", out.getCurrentStage());
        ArgumentCaptor<DesignSessionEntity> patch = ArgumentCaptor.forClass(DesignSessionEntity.class);
        verify(sessionMapper).update(patch.capture(), any());
        assertEquals("A1", patch.getValue().getCurrentStage());
        verify(taskPublisher).publishA1Task("s1");
    }
}
