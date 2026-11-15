package com.sonarcad.workflow;

import com.fasterxml.jackson.databind.JsonNode;
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
 * T15.6 / C-0 批准版本组合单测（ADR-014 §5，mock 持久层）：
 * gate2 批准固化 snapshot_combo+combo_policy 进 review_records.metadata（V20）；
 * fail 批准必须显式人工例外；expectedIr4Version 并发条件；knowledge 关 kbId 入档。
 * 打桩顺序 = 组合读取顺序：latestValidSnapshot IR2→IR3→IR4→（未决项收集）IR1。
 */
class ReviewServiceComboTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    private DesignSessionMapper sessionMapper;
    private IrSnapshotMapper irSnapshotMapper;
    private ReviewRecordMapper reviewRecordMapper;
    private SessionService sessionService;
    private TaskPublisher taskPublisher;
    private KnowledgeWritebackClient writebackClient;
    private TraceChainMapper traceChainMapper;
    private ReviewService service;

    @BeforeEach
    void setUp() {
        sessionMapper = mock(DesignSessionMapper.class);
        irSnapshotMapper = mock(IrSnapshotMapper.class);
        reviewRecordMapper = mock(ReviewRecordMapper.class);
        sessionService = mock(SessionService.class);
        taskPublisher = mock(TaskPublisher.class);
        writebackClient = mock(KnowledgeWritebackClient.class);
        traceChainMapper = mock(TraceChainMapper.class);
        service = new ReviewService(sessionMapper, irSnapshotMapper, reviewRecordMapper,
                traceChainMapper, sessionService, taskPublisher, writebackClient,
                mock(UserLookupMapper.class));
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

    private void stubCombo(IrSnapshotEntity ir2, IrSnapshotEntity ir3, IrSnapshotEntity ir4,
                           IrSnapshotEntity ir1) {
        when(irSnapshotMapper.selectOne(any())).thenReturn(ir2, ir3, ir4, ir1);
    }

    private static String ir4Of(String overall, String... unverifiedIds) {
        var issues = new java.util.ArrayList<Map<String, Object>>();
        for (String id : unverifiedIds) {
            issues.add(Map.of("constraint_id", id, "severity", "unverified",
                    "related_params", List.of(), "related_features", List.of()));
        }
        try {
            return JSON.writeValueAsString(Map.of("overall", overall, "issues", issues));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private ReviewRecordEntity capturedRecord() {
        ArgumentCaptor<ReviewRecordEntity> captor = ArgumentCaptor.forClass(ReviewRecordEntity.class);
        verify(reviewRecordMapper).insert(captor.capture());
        return captor.getValue();
    }

    @Test
    void gate2ApproveRecordsComboMetadata() throws Exception {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        // "hello" 的 sha256 固定向量——验证哈希口径确为 sha256(content::text)
        stubCombo(snapshot("IR2", 2, "{}"), snapshot("IR3", 3, "hello"),
                snapshot("IR4", 1, ir4Of("pass")),
                snapshot("IR1", 1, "{\"missing_items\": [\"覆冰工况缺失\"]}"));

        service.decide("s1", "gate2", "approved", null, null, null, "r", null, null, null, null);

        ReviewRecordEntity record = capturedRecord();
        assertNotNull(record.getMetadata(), "gate2 批准必须落组合凭证");
        JsonNode meta = JSON.readTree(record.getMetadata());
        JsonNode combo = meta.path("snapshot_combo");
        assertEquals("IR2-2", combo.path("IR2").path("snapshot_id").asText());
        assertEquals(3, combo.path("IR3").path("version").asInt());
        assertEquals("IR4-1", combo.path("IR4").path("snapshot_id").asText());
        assertEquals("2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                combo.path("IR3").path("content_sha256").asText(), "sha256(content::text) 口径");
        assertEquals(64, combo.path("IR2").path("content_sha256").asText().length());
        assertFalse(combo.path("IR3").path("valid_at_decision").asText().isBlank());
        JsonNode policy = meta.path("combo_policy");
        assertEquals("pass", policy.path("ir4_overall").asText());
        assertTrue(policy.path("exception").isNull(), "非 fail 批准无例外");
        assertEquals("IR1", policy.path("unresolved").get(0).path("source").asText());
        assertEquals("覆冰工况缺失", policy.path("unresolved").get(0).path("ref").asText());
        // 被审主快照 = 组合内 IR3（二者永不分叉）
        assertEquals("IR3-3", record.getIrSnapshotId());
        verify(sessionService).resumeFromGate("s1", "A5");
        verify(taskPublisher).publishA5Task("s1");
    }

    @Test
    void gate2ApproveFailWithoutExceptionConflicts409() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        stubCombo(snapshot("IR2", 1, "{}"), snapshot("IR3", 1, "{}"),
                snapshot("IR4", 1, ir4Of("fail")), null);

        ResponseStatusException exc = assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "gate2", "approved", null, null, null, "r",
                        null, null, null, null));
        assertTrue(exc.getReason().contains("显式人工例外"), exc.getReason());
        verify(sessionService, never()).resumeFromGate(any(), any());
        verify(taskPublisher, never()).publishA5Task(any());
        verify(reviewRecordMapper, never()).insert(any(ReviewRecordEntity.class));
    }

    @Test
    void gate2ApproveFailWithExceptionRecordsPolicy() throws Exception {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        stubCombo(snapshot("IR2", 1, "{}"), snapshot("IR3", 1, "{}"),
                snapshot("IR4", 1, ir4Of("fail")),
                snapshot("IR1", 1, "{\"missing_items\": []}"));

        service.decide("s1", "gate2", "approved", null, null, null, "r",
                null, true, "军标未覆盖项经人工复核可接受", null);

        JsonNode meta = JSON.readTree(capturedRecord().getMetadata());
        assertEquals("fail", meta.path("combo_policy").path("ir4_overall").asText());
        assertEquals(true, meta.path("combo_policy").path("exception").path("granted").asBoolean());
        assertEquals("军标未覆盖项经人工复核可接受",
                meta.path("combo_policy").path("exception").path("reason").asText());
    }

    @Test
    void gate2ApproveIncompleteRecordsUnresolvedFromIr4AndIr1() throws Exception {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        stubCombo(snapshot("IR2", 1, "{}"), snapshot("IR3", 1, "{}"),
                snapshot("IR4", 1, ir4Of("incomplete", "CHK-SEM-DIAMETER", "CHK-STD-UNCOVERED")),
                snapshot("IR1", 1, "{\"missing_items\": [\"耐压指标缺失\"]}"));

        service.decide("s1", "gate2", "approved", null, null, null, "r", null, null, null, null);

        JsonNode unresolved = JSON.readTree(capturedRecord().getMetadata())
                .path("combo_policy").path("unresolved");
        assertEquals(3, unresolved.size());
        assertEquals("IR4", unresolved.get(0).path("source").asText());
        assertEquals("CHK-SEM-DIAMETER", unresolved.get(0).path("ref").asText());
        assertEquals("CHK-STD-UNCOVERED", unresolved.get(1).path("ref").asText());
        assertEquals("IR1", unresolved.get(2).path("source").asText());
        assertEquals("耐压指标缺失", unresolved.get(2).path("ref").asText());
    }

    @Test
    void gate2ApproveStaleIr4VersionConflicts409() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        stubCombo(snapshot("IR2", 1, "{}"), snapshot("IR3", 1, "{}"),
                snapshot("IR4", 2, ir4Of("pass")), null);

        ResponseStatusException exc = assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "gate2", "approved", null, null, null, "r",
                        null, null, null, 1));
        assertTrue(exc.getReason().contains("SNAPSHOT_STALE"), exc.getReason());
        assertTrue(exc.getReason().contains("IR4"), exc.getReason());
        verify(reviewRecordMapper, never()).insert(any(ReviewRecordEntity.class));
    }

    @Test
    void gate2ApproveExceptionWithoutFailRejected() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        stubCombo(snapshot("IR2", 1, "{}"), snapshot("IR3", 1, "{}"),
                snapshot("IR4", 1, ir4Of("pass")), null);

        assertThrows(IllegalArgumentException.class,
                () -> service.decide("s1", "gate2", "approved", null, null, null, "r",
                        null, true, "多余的例外", null));
        verify(reviewRecordMapper, never()).insert(any(ReviewRecordEntity.class));
    }

    @Test
    void gate2ApproveMissingIr2OrIr3Conflicts409() {
        // 被审主对象（IR3）与上游方案（IR2）必须有效在位——缺失仍 409（不同于 IR4 的诚实缺省）
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        when(irSnapshotMapper.selectOne(any()))
                .thenReturn(snapshot("IR2", 1, "{}"))
                .thenReturn(null);

        ResponseStatusException exc = assertThrows(ResponseStatusException.class,
                () -> service.decide("s1", "gate2", "approved", null, null, null, "r",
                        null, null, null, null));
        assertTrue(exc.getReason().contains("IR3"), exc.getReason());
        verify(reviewRecordMapper, never()).insert(any(ReviewRecordEntity.class));
    }

    @Test
    void gate2ApproveWithMissingIr4RecordsHonestCombo() throws Exception {
        // T14.5 语义：IR4 无任何版本（异常路径）→ 不阻断批准，组合如实记 missing + unresolved
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        when(irSnapshotMapper.selectOne(any()))
                .thenReturn(snapshot("IR2", 1, "{}"))
                .thenReturn(snapshot("IR3", 1, "{}"))
                .thenReturn(null)          // latestValidSnapshot IR4 → 无有效版
                .thenReturn(null);         // latestSnapshot IR4 → 无任何版本

        service.decide("s1", "gate2", "approved", null, null, null, "r", null, null, null, null);

        JsonNode meta = JSON.readTree(capturedRecord().getMetadata());
        assertFalse(meta.path("snapshot_combo").has("IR4"), "IR4 缺失 → 组合不含该条目（诚实缺省）");
        assertEquals("missing", meta.path("combo_policy").path("ir4_overall").asText());
        assertEquals("IR4", meta.path("combo_policy").path("unresolved").get(0).path("source").asText());
        verify(sessionService).resumeFromGate("s1", "A5");
    }

    @Test
    void gate2ApproveWithInvalidatedIr4RecordsHonestComboNoExceptionNeeded() throws Exception {
        // T14.5 微调定稿失效语义：IR4 最新版已失效 → 组合记 valid=false + ir4_overall=invalidated，
        // 批准不阻断、无需例外（失效报告的 fail 结论不再治理当前批准）
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        IrSnapshotEntity stale = snapshot("IR4", 1, ir4Of("fail"));
        stale.setInvalidatedAt(java.time.OffsetDateTime.now());
        when(irSnapshotMapper.selectOne(any()))
                .thenReturn(snapshot("IR2", 1, "{}"))
                .thenReturn(snapshot("IR3", 1, "{}"))
                .thenReturn(null)          // latestValidSnapshot IR4 → 无有效版
                .thenReturn(stale);        // latestSnapshot IR4 → 失效行

        service.decide("s1", "gate2", "approved", null, null, null, "r", null, null, null, null);

        JsonNode meta = JSON.readTree(capturedRecord().getMetadata());
        assertEquals(false, meta.path("snapshot_combo").path("IR4").path("valid").asBoolean());
        assertEquals("invalidated", meta.path("combo_policy").path("ir4_overall").asText());
        assertTrue(meta.path("combo_policy").path("exception").isNull());
        assertTrue(meta.path("combo_policy").path("unresolved").get(0).path("ref").asText().contains("已失效"));
    }

    @Test
    void gate2ApproveInvalidatedIr4RejectsExceptionRequest() {
        // IR4 失效/缺失时无 fail 结论可例外——携带例外 → 400
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        IrSnapshotEntity stale = snapshot("IR4", 1, ir4Of("fail"));
        stale.setInvalidatedAt(java.time.OffsetDateTime.now());
        when(irSnapshotMapper.selectOne(any()))
                .thenReturn(snapshot("IR2", 1, "{}"))
                .thenReturn(snapshot("IR3", 1, "{}"))
                .thenReturn(null)
                .thenReturn(stale);

        assertThrows(IllegalArgumentException.class,
                () -> service.decide("s1", "gate2", "approved", null, null, null, "r",
                        null, true, "无效例外", null));
        verify(reviewRecordMapper, never()).insert(any(ReviewRecordEntity.class));
    }

    @Test
    void gate2RejectKeepsMetadataNull() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate2"));
        // 驳回路径：被审 IR3 →（resolveRework）IR4 suggestion 推断
        when(irSnapshotMapper.selectOne(any()))
                .thenReturn(snapshot("IR3", 1, "{}"))
                .thenReturn(snapshot("IR4", 1, ir4Of("fail")));

        service.decide("s1", "gate2", "rejected", List.of("驳回"), "A3", null, "r", null,
                null, null, null);

        ReviewRecordEntity record = capturedRecord();
        assertNull(record.getMetadata(), "驳回无组合诉求，metadata 保持 NULL=组合契约前语义");
        verify(irSnapshotMapper).markInvalidated(eq("s1"), eq(List.of("IR3", "IR4", "IR5")));
    }

    @Test
    void gate1ApproveUnaffectedByCombo() {
        when(sessionMapper.selectById("s1")).thenReturn(session("paused", "gate1"));
        when(irSnapshotMapper.selectOne(any())).thenReturn(snapshot("IR2", 1, "{}"));

        service.decide("s1", "gate1", "approved", null, null, null, "r", null, null, null, null);

        ReviewRecordEntity record = capturedRecord();
        assertNull(record.getMetadata(), "gate1 不做组合固化（被审对象=IR2 单快照）");
        verify(taskPublisher).publishA3Task("s1");
    }

    @Test
    void knowledgeGateRecordsKbIdMetadata() throws Exception {
        when(sessionMapper.selectById("s1")).thenReturn(session("completed", "A5"));
        when(irSnapshotMapper.selectOne(any()))
                .thenReturn(snapshot("IR5", 2, "{\"writeback_status\": \"pending\"}"));
        when(writebackClient.writeback("s1", "kb-9")).thenReturn(Map.of("written", Map.of("written", 1)));

        service.decide("s1", "knowledge", "approved", null, null, "kb-9", "r", null, null, null, null);

        JsonNode meta = JSON.readTree(capturedRecord().getMetadata());
        assertEquals("IR5-2", meta.path("ir5_snapshot_id").asText());
        assertEquals(2, meta.path("ir5_version").asInt());
        assertEquals("kb-9", meta.path("kb_id").asText());
    }
}
