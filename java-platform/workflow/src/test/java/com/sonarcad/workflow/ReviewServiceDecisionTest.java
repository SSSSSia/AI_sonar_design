package com.sonarcad.workflow;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sonarcad.workflow.mq.TaskPublisher;
import com.sonarcad.workflow.session.*;
import com.sonarcad.workflow.web.KnowledgeWritebackClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * T2.2 审阅裁决单测（mock 持久层）：通过流 / 驳回局部回算 + 下游失效 / 知识审阅关回写与失败如实记录。
 */
class ReviewServiceDecisionTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    private DesignSessionMapper sessionMapper;
    private IrSnapshotMapper irSnapshotMapper;
    private ReviewRecordMapper reviewRecordMapper;
    private SessionService sessionService;
    private TaskPublisher taskPublisher;
    private KnowledgeWritebackClient writebackClient;
    private com.sonarcad.workflow.session.TraceChainMapper traceChainMapper;
    private ReviewService service;

    @BeforeEach
    void setUp() {
        sessionMapper = mock(DesignSessionMapper.class);
        irSnapshotMapper = mock(IrSnapshotMapper.class);
        reviewRecordMapper = mock(ReviewRecordMapper.class);
        sessionService = mock(SessionService.class);
        taskPublisher = mock(TaskPublisher.class);
        writebackClient = mock(KnowledgeWritebackClient.class);
        traceChainMapper = mock(com.sonarcad.workflow.session.TraceChainMapper.class);
        service = new ReviewService(sessionMapper, irSnapshotMapper, reviewRecordMapper,
                traceChainMapper, sessionService, taskPublisher, writebackClient,
                mock(com.sonarcad.workflow.session.UserLookupMapper.class));
    }

    private DesignSessionEntity session(String status, String stage) {
        DesignSessionEntity s = new DesignSessionEntity();
        s.setId("s1");
        s.setPipelineStatus(status);
        s.setCurrentStage(stage);
        return s;
    }

    private IrSnapshotEntity snapshot(String irType, int version, String content) {
        IrSnapshotEntity e = new IrSnapshotEntity();
        e.setId(irType + "-" + version);
        e.setSessionId("s1");
        e.setIrType(irType);
        e.setVersion(version);
        e.setSchemaVersion("v1");
        e.setContent(content);
        return e;
    }

    @Test
    void gate1ApproveResumesToA3AndPublishes() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate1"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(snapshot("IR2", 1, "{}"));

        Map<String, Object> effects = service.decide("s1", "gate1", "approved", null, null, null, "reviewer-1", null);

        verify(sessionService).resumeFromGate("s1", "A3");
        verify(taskPublisher).publishA3Task("s1");
        assertEquals("approved", effects.get("decision"));
        ArgumentCaptor<ReviewRecordEntity> record = ArgumentCaptor.forClass(ReviewRecordEntity.class);
        verify(reviewRecordMapper).insert(record.capture());
        assertEquals("gate1", record.getValue().getGate());
        assertEquals("approved", record.getValue().getDecision());
        assertEquals("IR2-1", record.getValue().getIrSnapshotId());
        // T2.4：裁决同步写溯源 confirm 行（合成 trace_id rv- 前缀）
        ArgumentCaptor<com.sonarcad.workflow.session.TraceChainEntity> trace =
                ArgumentCaptor.forClass(com.sonarcad.workflow.session.TraceChainEntity.class);
        verify(traceChainMapper).insert(trace.capture());
        assertTrue(trace.getValue().getTraceId().startsWith("rv-"));
        assertEquals("review", trace.getValue().getActor());
        assertEquals("confirm", trace.getValue().getAction());
    }

    @Test
    void gate2RejectFollowsIr4SuggestionBackToA2AndInvalidates() throws Exception {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        String ir4 = JSON.writeValueAsString(Map.of("overall", "fail", "issues", List.of(
                Map.of("constraint_id", "C1", "severity", "error",
                        "related_params", List.of("P-1"), "related_features", List.of(),
                        "suggestion", "back_to_A2"))));
        when(irSnapshotMapper.selectOne(any()))
                .thenReturn(snapshot("IR3", 1, "{}"))   // 被审主快照
                .thenReturn(snapshot("IR4", 1, ir4));   // 驳回目标推断

        service.decide("s1", "gate2", "rejected", List.of("参数不合理"), null, null, "reviewer-1", null);

        verify(sessionService).resumeFromGate("s1", "A2");
        verify(irSnapshotMapper).markInvalidated(eq("s1"), eq(List.of("IR2", "IR3", "IR4", "IR5")));
        verify(taskPublisher).publishA2Task("s1");
        ArgumentCaptor<ReviewRecordEntity> record = ArgumentCaptor.forClass(ReviewRecordEntity.class);
        verify(reviewRecordMapper).insert(record.capture());
        assertTrue(record.getValue().getAnnotations().contains("back_to_A2")
                || record.getValue().getAnnotations().contains("A2"));
    }

    @Test
    void gate2RejectExplicitReworkA3InvalidatesFromIR3() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(snapshot("IR3", 2, "{}"));

        Map<String, Object> effects = service.decide("s1", "gate2", "rejected", null, "A3", null, "reviewer-1", null);

        verify(sessionService).resumeFromGate("s1", "A3");
        verify(irSnapshotMapper).markInvalidated(eq("s1"), eq(List.of("IR3", "IR4", "IR5")));
        verify(taskPublisher).publishA3Task("s1");
        assertEquals("A3", effects.get("rework_stage"));
    }

    @Test
    void gateDecisionOutsidePauseConflicts409() {
        when(sessionMapper.selectById("s1")).thenReturn(session("running", "A2"));

        assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "gate1", "approved", null, null, null, "r", null));
        verify(taskPublisher, never()).publishA3Task(any());
    }

    @Test
    void gate2RejectWithInvalidReworkTargetRejected() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(snapshot("IR3", 1, "{}"));

        assertThrows(IllegalArgumentException.class,
                () -> service.decide("s1", "gate2", "rejected", null, "A1", null, "r", null));
    }

    @Test
    void knowledgeApproveCallsWritebackAndRecords() {
        when(sessionMapper.selectById("s1")).thenReturn(session("completed", "A5"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(
                snapshot("IR5", 1, "{\"writeback_status\": \"pending\"}"));
        when(writebackClient.writeback("s1", null)).thenReturn(Map.of("written", Map.of("written", 2)));

        Map<String, Object> effects = service.decide("s1", "knowledge", "approved", null, null, null, "r", null);

        verify(writebackClient).writeback("s1", null);
        assertEquals(effects.get("writeback"), Map.of("written", Map.of("written", 2)));
        ArgumentCaptor<ReviewRecordEntity> record = ArgumentCaptor.forClass(ReviewRecordEntity.class);
        verify(reviewRecordMapper).insert(record.capture());
        assertEquals("knowledge", record.getValue().getGate());
        // 回写结果以摘要字符串入库（整对象入库曾致前端 [object Object]）
        assertTrue(record.getValue().getAnnotations().contains("已写入图谱"));
    }

    @Test
    void knowledgeApproveWritebackFailureStillRecordsDecision() {
        when(sessionMapper.selectById("s1")).thenReturn(session("completed", "A5"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(
                snapshot("IR5", 1, "{\"writeback_status\": \"pending\"}"));
        when(writebackClient.writeback("s1", null)).thenThrow(new RuntimeException("Neo4j down"));

        Map<String, Object> effects = service.decide("s1", "knowledge", "approved", null, null, null, "r", null);

        assertEquals("failed", effects.get("writeback"));
        ArgumentCaptor<ReviewRecordEntity> record = ArgumentCaptor.forClass(ReviewRecordEntity.class);
        verify(reviewRecordMapper).insert(record.capture());
        assertTrue(record.getValue().getAnnotations().contains("Neo4j down"));
    }

    @Test
    void knowledgeRejectSkipsWriteback() {
        when(sessionMapper.selectById("s1")).thenReturn(session("completed", "A5"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(
                snapshot("IR5", 1, "{\"writeback_status\": \"pending\"}"));

        service.decide("s1", "knowledge", "rejected", null, null, null, "r", null);

        // 跳过=显式终态：调 py-engine 将 IR5 pending→skipped（面板随之收敛，可补做）
        verify(writebackClient).skip("s1");
        ArgumentCaptor<ReviewRecordEntity> record = ArgumentCaptor.forClass(ReviewRecordEntity.class);
        verify(reviewRecordMapper).insert(record.capture());
        assertTrue(record.getValue().getAnnotations().contains("skipped"));
    }

    @Test
    void knowledgeRejectIdempotentWhenNotPending() {
        // 重复点击防堆积：非 pending 再跳过 409
        when(sessionMapper.selectById("s1")).thenReturn(session("completed", "A5"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(
                snapshot("IR5", 2, "{\"writeback_status\": \"skipped\"}"));
        assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "knowledge", "rejected", null, null, null, "r", null));
        verifyNoInteractions(writebackClient);
        verify(reviewRecordMapper, never()).insert(any(ReviewRecordEntity.class));
    }

    @Test
    void knowledgeApproveAllowedFromSkippedForRedo() {
        when(sessionMapper.selectById("s1")).thenReturn(session("completed", "A5"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(
                snapshot("IR5", 2, "{\"writeback_status\": \"skipped\"}"));
        when(writebackClient.writeback("s1", null)).thenReturn(Map.of("ir_version", 3));

        Map<String, Object> effects = service.decide("s1", "knowledge", "approved", null, null, null, "r", null);

        verify(writebackClient).writeback("s1", null);
        assertEquals(effects.get("writeback"), Map.of("ir_version", 3));
    }

    @Test
    void knowledgeGateRequiresCompletedSessionAndPendingIr5() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate1"));
        assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "knowledge", "approved", null, null, null, "r", null));

        when(sessionMapper.selectById("s1")).thenReturn(session("completed", "A5"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(
                snapshot("IR5", 2, "{\"writeback_status\": \"written\"}"));
        assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "knowledge", "approved", null, null, null, "r", null));
    }

    @Test
    void invalidDecisionOrGateRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> service.decide("s1", "gate1", "maybe", null, null, null, "r", null));
        assertThrows(IllegalArgumentException.class,
                () -> service.decide("s1", "gate9", "approved", null, null, null, "r", null));
    }

    // ------------------------------------------------------------------
    // T14.6 审阅版本条件（外部审查问题 4c）：旧页面不能批准新模型
    // ------------------------------------------------------------------
    @Test
    void staleExpectedVersionConflicts409() {
        // 审阅人看到 IR2 v1，后台已生成 v2 → 裁决 409 SNAPSHOT_STALE
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate1"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(snapshot("IR2", 2, "{}"));

        ResponseStatusException exc = assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "gate1", "approved", null, null, null, "r", 1));
        assertTrue(exc.getReason().contains("SNAPSHOT_STALE"), exc.getReason());
        assertTrue(exc.getReason().contains("v1") && exc.getReason().contains("v2"));
        verify(taskPublisher, never()).publishA3Task(any());
        verify(reviewRecordMapper, never()).insert(any(ReviewRecordEntity.class));
    }

    @Test
    void matchingExpectedVersionPassesThrough() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate1"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(snapshot("IR2", 2, "{}"));

        service.decide("s1", "gate1", "approved", null, null, null, "r", 2);

        verify(sessionService).resumeFromGate("s1", "A3");
    }

    @Test
    void knowledgeGateAlsoGuardsExpectedVersion() {
        when(sessionMapper.selectById("s1")).thenReturn(session("completed", "A5"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(
                snapshot("IR5", 2, "{\"writeback_status\": \"pending\"}"));

        assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "knowledge", "rejected", null, null, null, "r", 1));
        verifyNoInteractions(writebackClient);
    }
}
