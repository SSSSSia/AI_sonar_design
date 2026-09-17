package com.sonarcad.workflow;

import com.sonarcad.workflow.mq.TaskPublisher;
import com.sonarcad.workflow.session.DesignSessionEntity;
import com.sonarcad.workflow.session.DesignSessionMapper;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.session.SessionService;
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
    private SessionService service;

    @BeforeEach
    void setUp() {
        sessionMapper = mock(DesignSessionMapper.class);
        irSnapshotMapper = mock(IrSnapshotMapper.class);
        taskPublisher = mock(TaskPublisher.class);
        service = new SessionService(sessionMapper, irSnapshotMapper, taskPublisher);
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
}
