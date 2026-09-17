package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sonarcad.workflow.mq.TaskPublisher;
import com.sonarcad.workflow.statemachine.PipelineStatus;
import com.sonarcad.workflow.web.KnowledgeWritebackClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 审阅裁决服务（T2.2，宪法 A-11/A-12）。
 *
 * 门语义：每份新 IR2（gate1）/IR3+IR4（gate2）都重新触发对应审阅关；驳回走局部回算——
 * 按重跑目标重派任务，并把目标产物及其下游快照整批标 invalidated_at（内容不可变）。
 * 全部数据库操作单事务；MQ 投递在事务内最后一步（失败整体回滚，用户重试）。
 */
@Service
public class ReviewService {

    private static final Logger log = LoggerFactory.getLogger(ReviewService.class);
    private static final ObjectMapper JSON = new ObjectMapper();

    private final DesignSessionMapper sessionMapper;
    private final IrSnapshotMapper irSnapshotMapper;
    private final ReviewRecordMapper reviewRecordMapper;
    private final SessionService sessionService;
    private final TaskPublisher taskPublisher;
    private final KnowledgeWritebackClient writebackClient;

    public ReviewService(DesignSessionMapper sessionMapper, IrSnapshotMapper irSnapshotMapper,
                         ReviewRecordMapper reviewRecordMapper, SessionService sessionService,
                         TaskPublisher taskPublisher, KnowledgeWritebackClient writebackClient) {
        this.sessionMapper = sessionMapper;
        this.irSnapshotMapper = irSnapshotMapper;
        this.reviewRecordMapper = reviewRecordMapper;
        this.sessionService = sessionService;
        this.taskPublisher = taskPublisher;
        this.writebackClient = writebackClient;
    }

    /** 裁决入口。gate ∈ gate1|gate2|knowledge；decision ∈ approved|rejected。 */
    @Transactional
    public Map<String, Object> decide(String sessionId, String gate, String decision,
                                      List<Object> annotations, String reworkStage, String reviewerId) {
        validateGate(gate);
        if (!"approved".equals(decision) && !"rejected".equals(decision)) {
            throw new IllegalArgumentException("decision 只允许 approved|rejected: " + decision);
        }
        DesignSessionEntity session = getSession(sessionId);
        PipelineStatus status = PipelineStatus.valueOf(session.getPipelineStatus());

        Map<String, Object> effects = new LinkedHashMap<>();
        effects.put("gate", gate);
        effects.put("decision", decision);

        String snapshotIrType = switch (gate) {
            case "gate1" -> "IR2";
            case "gate2" -> "IR3";
            default -> "IR5"; // knowledge
        };

        if ("knowledge".equals(gate)) {
            decideKnowledgeGate(sessionId, session, status, decision, annotations, reviewerId, effects);
            return effects;
        }

        // ---- gate1 / gate2：要求 paused 且停在对应关 ----
        if (status != PipelineStatus.paused || !gate.equals(session.getCurrentStage())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "会话未在 " + gate + " 暂停（当前 " + status + " / " + session.getCurrentStage() + "）");
        }
        IrSnapshotEntity snapshot = latestSnapshot(sessionId, snapshotIrType);
        if (snapshot == null) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    gate + " 缺少被审快照 " + snapshotIrType);
        }

        String rework = null;
        List<String> invalidated = List.of();
        if ("rejected".equals(decision)) {
            rework = resolveRework(sessionId, gate, reworkStage);
            invalidated = invalidateDownstream(sessionId, rework);
            effects.put("rework_stage", rework);
            effects.put("invalidated", invalidated);
        }

        // 恢复运行并标记下一棒（乐观并发：并发双裁决在此收敛 409）
        String nextStage = "approved".equals(decision)
                ? ("gate1".equals(gate) ? "A3" : "A5")
                : rework;
        sessionService.resumeFromGate(sessionId, nextStage);

        // 投递下一棒 / 重跑任务（事务内最后一步；失败整体回滚）
        switch (nextStage) {
            case "A1" -> taskPublisher.publishA1Task(sessionId);
            case "A2" -> taskPublisher.publishA2Task(sessionId);
            case "A3" -> taskPublisher.publishA3Task(sessionId);
            case "A5" -> taskPublisher.publishA5Task(sessionId);
            default -> throw new IllegalStateException("未知下一棒: " + nextStage);
        }

        ReviewRecordEntity record = insertRecord(sessionId, gate, snapshot.getId(), decision,
                annotations, reviewerId, Map.of(
                        "reviewed_ir", snapshotIrType + " v" + snapshot.getVersion(),
                        "rework_stage", rework == null ? "" : rework,
                        "invalidated", invalidated));
        effects.put("record_id", record.getId());
        effects.put("resumed_to", nextStage);
        return effects;
    }

    // ------------------------------------------------------------------
    private void decideKnowledgeGate(String sessionId, DesignSessionEntity session,
                                     PipelineStatus status, String decision,
                                     List<Object> annotations, String reviewerId,
                                     Map<String, Object> effects) {
        if (status != PipelineStatus.completed) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "知识审阅关在会话 completed 后裁决（当前 " + status + "）");
        }
        IrSnapshotEntity ir5 = latestSnapshot(sessionId, "IR5");
        if (ir5 == null) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "缺少 IR5 快照");
        }
        if (!"pending".equals(readJsonField(ir5.getContent(), "writeback_status"))) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "IR5 非 pending 状态，无可回写候选");
        }
        if ("rejected".equals(decision)) {
            ReviewRecordEntity record = insertRecord(sessionId, "knowledge", ir5.getId(), decision,
                    annotations, reviewerId, Map.of("writeback", "skipped（裁决驳回，不回写）"));
            effects.put("record_id", record.getId());
            return;
        }
        // approved：同步调用 py-engine 回写；失败如实记录（决策已落库，IR5 保持 pending 可重试）
        Map<String, Object> auto = new LinkedHashMap<>();
        auto.put("reviewed_ir", "IR5 v" + ir5.getVersion());
        try {
            Object result = writebackClient.writeback(sessionId);
            auto.put("writeback", result);
            effects.put("writeback", result);
        } catch (Exception exc) {
            log.error("知识回写失败 session={}——决策已记录，IR5 保持 pending", sessionId, exc);
            auto.put("writeback", "failed: " + exc.getMessage());
            effects.put("writeback", "failed");
        }
        ReviewRecordEntity record = insertRecord(sessionId, "knowledge", ir5.getId(), decision,
                annotations, reviewerId, auto);
        effects.put("record_id", record.getId());
    }

    // ------------------------------------------------------------------
    /** 驳回重跑目标：请求覆盖优先；否则 gate1→A2，gate2 按 IR4 issues 的 suggestion（back_to_A2 优先）。 */
    private String resolveRework(String sessionId, String gate, String reworkStage) {
        if (reworkStage != null && !reworkStage.isBlank()) {
            List<String> allowed = "gate1".equals(gate) ? List.of("A1", "A2") : List.of("A2", "A3");
            if (!allowed.contains(reworkStage)) {
                throw new IllegalArgumentException(gate + " 驳回重跑目标只允许 " + allowed + ": " + reworkStage);
            }
            return reworkStage;
        }
        if ("gate1".equals(gate)) {
            return "A2";
        }
        IrSnapshotEntity ir4 = latestSnapshot(sessionId, "IR4");
        if (ir4 != null && ir4.getContent() != null && ir4.getContent().contains("back_to_A2")) {
            return "A2";
        }
        return "A3";
    }

    /** 下游失效（A-12）：重跑目标 T 的产物及其下游整批标记（内容不变）。 */
    private List<String> invalidateDownstream(String sessionId, String rework) {
        List<String> all = List.of("IR1", "IR2", "IR3", "IR4", "IR5");
        List<String> types = new ArrayList<>(all.subList(all.indexOf(irOf(rework)), all.size()));
        irSnapshotMapper.markInvalidated(sessionId, types);
        return types;
    }

    private static String irOf(String stage) {
        return switch (stage) {
            case "A1" -> "IR1";
            case "A2" -> "IR2";
            case "A3" -> "IR3";
            default -> throw new IllegalArgumentException("未知重跑目标: " + stage);
        };
    }

    // ------------------------------------------------------------------
    private ReviewRecordEntity insertRecord(String sessionId, String gate, String irSnapshotId,
                                            String decision, List<Object> annotations,
                                            String reviewerId, Map<String, Object> autoFields) {
        ReviewRecordEntity record = new ReviewRecordEntity();
        record.setSessionId(sessionId);
        record.setGate(gate);
        record.setIrSnapshotId(irSnapshotId);
        record.setDecision(decision);
        record.setReviewerId(reviewerId);
        record.setDecidedAt(OffsetDateTime.now());
        Map<String, Object> annotationPayload = new LinkedHashMap<>();
        annotationPayload.put("annotations", annotations == null ? List.of() : annotations);
        annotationPayload.putAll(autoFields);
        record.setAnnotations(toJson(annotationPayload));
        reviewRecordMapper.insert(record);
        return record;
    }

    public List<ReviewRecordEntity> list(String sessionId) {
        return reviewRecordMapper.selectList(new LambdaQueryWrapper<ReviewRecordEntity>()
                .eq(ReviewRecordEntity::getSessionId, sessionId)
                .orderByDesc(ReviewRecordEntity::getDecidedAt));
    }

    // ------------------------------------------------------------------
    private DesignSessionEntity getSession(String id) {
        DesignSessionEntity s = sessionMapper.selectById(id);
        if (s == null) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "会话不存在: " + id);
        return s;
    }

    private IrSnapshotEntity latestSnapshot(String sessionId, String irType) {
        return irSnapshotMapper.selectOne(new LambdaQueryWrapper<IrSnapshotEntity>()
                .eq(IrSnapshotEntity::getSessionId, sessionId)
                .eq(IrSnapshotEntity::getIrType, irType)
                .orderByDesc(IrSnapshotEntity::getVersion)
                .last("LIMIT 1"));
    }

    private String readJsonField(String json, String field) {
        try {
            Object v = JSON.readValue(json == null ? "{}" : json, Map.class).get(field);
            return v == null ? null : String.valueOf(v);
        } catch (Exception e) {
            return null;
        }
    }

    private String toJson(Object value) {
        try {
            return JSON.writeValueAsString(value);
        } catch (Exception e) {
            return "{\"annotations\": []}";
        }
    }

    private static void validateGate(String gate) {
        if (!"gate1".equals(gate) && !"gate2".equals(gate) && !"knowledge".equals(gate)) {
            throw new IllegalArgumentException("非法审阅关: " + gate + "（gate1|gate2|knowledge）");
        }
    }
}
