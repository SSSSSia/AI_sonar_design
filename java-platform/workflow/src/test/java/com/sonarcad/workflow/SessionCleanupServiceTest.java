package com.sonarcad.workflow;

import com.sonarcad.workflow.session.CadArtifactsClient;
import com.sonarcad.workflow.session.DesignSessionEntity;
import com.sonarcad.workflow.session.DesignSessionMapper;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.session.ReviewRecordMapper;
import com.sonarcad.workflow.session.SessionCleanupService;
import com.sonarcad.workflow.session.TraceChainMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.InOrder;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/** 会话删除规则（2026-09-19）：终态直删级联三表；运行中/暂停须 force。 */
class SessionCleanupServiceTest {

    private DesignSessionMapper sessionMapper;
    private IrSnapshotMapper irMapper;
    private ReviewRecordMapper reviewMapper;
    private TraceChainMapper traceMapper;
    private CadArtifactsClient cadClient;
    private SessionCleanupService service;

    @BeforeEach
    void setUp() {
        sessionMapper = mock(DesignSessionMapper.class);
        irMapper = mock(IrSnapshotMapper.class);
        reviewMapper = mock(ReviewRecordMapper.class);
        traceMapper = mock(TraceChainMapper.class);
        cadClient = mock(CadArtifactsClient.class);
        when(cadClient.purgeQuietly(anyString())).thenReturn(Map.of("minio_objects_removed", 0));
        service = new SessionCleanupService(sessionMapper, irMapper, reviewMapper, traceMapper, cadClient);
    }

    private DesignSessionEntity session(String id, String status) {
        DesignSessionEntity s = new DesignSessionEntity();
        s.setId(id);
        s.setPipelineStatus(status);
        return s;
    }

    @Test
    void runningSessionRefusedWithoutForce() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running"));

        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> service.delete("s1", false));
        assertEquals(409, ex.getStatusCode().value());
        verify(sessionMapper, never()).deleteById(anyString());
    }

    @Test
    void forceDeletesRunningSession() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running"));
        when(sessionMapper.deleteById("s1")).thenReturn(1);

        Map<String, Object> r = service.delete("s1", true);

        assertEquals(true, r.get("deleted"));
        verify(sessionMapper).deleteById("s1");
    }

    @Test
    void completedSessionCascadesThreeTables() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "completed"));
        when(irMapper.delete(any())).thenReturn(6);
        when(reviewMapper.delete(any())).thenReturn(2);
        when(traceMapper.delete(any())).thenReturn(9);
        when(sessionMapper.deleteById("s1")).thenReturn(1);

        Map<String, Object> r = service.delete("s1", false);

        assertEquals(true, r.get("deleted"));
        assertEquals(6, r.get("ir_snapshots"));
        assertEquals(2, r.get("review_records"));
        assertEquals(9, r.get("trace_chain_rows"));
        verify(cadClient).purgeQuietly(eq("s1"));
    }

    /** 外键依赖顺序（2026-09-20 用户实测 FK 违约修复）：review/trace 先于 snapshots 删除。 */
    @Test
    void childrenDeletedBeforeSnapshots() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "completed"));
        when(sessionMapper.deleteById("s1")).thenReturn(1);

        service.delete("s1", false);

        InOrder order = inOrder(reviewMapper, traceMapper, irMapper, sessionMapper);
        order.verify(reviewMapper).delete(any());
        order.verify(traceMapper).delete(any());
        order.verify(irMapper).delete(any());
        order.verify(sessionMapper).deleteById("s1");
    }

    @Test
    void missingSessionIs404() {
        when(sessionMapper.selectById("nope")).thenReturn(null);
        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> service.delete("nope", false));
        assertEquals(404, ex.getStatusCode().value());
    }
}
