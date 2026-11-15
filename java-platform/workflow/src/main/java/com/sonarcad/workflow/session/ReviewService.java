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
import java.util.UUID;

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
    private final TraceChainMapper traceChainMapper;
    private final SessionService sessionService;
    private final TaskPublisher taskPublisher;
    private final KnowledgeWritebackClient writebackClient;
    private final UserLookupMapper userLookupMapper;

    public ReviewService(DesignSessionMapper sessionMapper, IrSnapshotMapper irSnapshotMapper,
                         ReviewRecordMapper reviewRecordMapper, TraceChainMapper traceChainMapper,
                         SessionService sessionService,
                         TaskPublisher taskPublisher, KnowledgeWritebackClient writebackClient,
                         UserLookupMapper userLookupMapper) {
        this.sessionMapper = sessionMapper;
        this.irSnapshotMapper = irSnapshotMapper;
        this.reviewRecordMapper = reviewRecordMapper;
        this.traceChainMapper = traceChainMapper;
        this.sessionService = sessionService;
        this.taskPublisher = taskPublisher;
        this.writebackClient = writebackClient;
        this.userLookupMapper = userLookupMapper;
    }

    /** 裁决入口。gate ∈ gate1|gate2|knowledge；decision ∈ approved|rejected。
     *  T14.6：expectedSnapshotVersion 非空时做乐观版本条件（审查问题 4c）——与被审
     *  快照最新版本不符 → 409 SNAPSHOT_STALE，不允许旧页面批准新模型。 */
    @Transactional
    public Map<String, Object> decide(String sessionId, String gate, String decision,
                                      List<Object> annotations, String reworkStage, String kbId,
                                      String reviewerId, Integer expectedSnapshotVersion) {
        return decide(sessionId, gate, decision, annotations, reworkStage, kbId, reviewerId,
                expectedSnapshotVersion, null, null, null);
    }

    /** T15.6/C-0 完整裁决入口（ADR-014 §5）：gate2 批准须过组合固化与政策校验——IR4 overall=fail
     *  必须携带显式人工例外（exceptionGranted=true + 非空 exceptionReason，否则 409 不得静默放行）；
     *  expectedIr4Version 为 IR4 并发条件（防批准冻结进过期校验报告）。组合落 review_records.metadata（V20）。 */
    @Transactional
    public Map<String, Object> decide(String sessionId, String gate, String decision,
                                      List<Object> annotations, String reworkStage, String kbId,
                                      String reviewerId, Integer expectedSnapshotVersion,
                                      Boolean exceptionGranted, String exceptionReason,
                                      Integer expectedIr4Version) {
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
            decideKnowledgeGate(sessionId, session, status, decision, annotations, kbId,
                    reviewerId, expectedSnapshotVersion, effects);
            return effects;
        }

        // ---- gate1 / gate2：要求 paused 且停在对应关 ----
        if (status != PipelineStatus.paused || !gate.equals(session.getCurrentStage())) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "会话未在 " + gate + " 暂停（当前 " + status + " / " + session.getCurrentStage() + "）");
        }
        // C-0（T15.6）：gate2 批准先固化批准组合——组合内 IR3（最新有效版）即被审主快照，二者永不分叉；
        // 其余路径（gate1 批准 / 各关驳回）维持原 latestSnapshot 语义，无组合诉求
        String metadataJson = null;
        IrSnapshotEntity snapshot;
        if ("gate2".equals(gate) && "approved".equals(decision)) {
            Gate2Combo combo = buildGate2Combo(sessionId, exceptionGranted, exceptionReason,
                    expectedIr4Version);
            snapshot = combo.ir3();
            metadataJson = combo.metadataJson();
        } else {
            snapshot = latestSnapshot(sessionId, snapshotIrType);
            if (snapshot == null) {
                throw new ResponseStatusException(HttpStatus.CONFLICT,
                        gate + " 缺少被审快照 " + snapshotIrType);
            }
        }
        assertExpectedVersion(snapshotIrType, expectedSnapshotVersion, snapshot.getVersion());

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
                        "invalidated", invalidated), metadataJson);
        effects.put("record_id", record.getId());
        effects.put("resumed_to", nextStage);
        return effects;
    }

    // ------------------------------------------------------------------
    /** T14.6 乐观版本条件（审查问题 4c）：审阅人看到的版本 ≠ 当前最新 → 409。 */
    private void assertExpectedVersion(String irType, Integer expected, int latest) {
        if (expected != null && expected != latest) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "SNAPSHOT_STALE: 您审阅的 " + irType + " v" + expected
                            + " 已不是最新（当前 v" + latest + "），请刷新复核后重新裁决");
        }
    }

    private void decideKnowledgeGate(String sessionId, DesignSessionEntity session,
                                     PipelineStatus status, String decision,
                                     List<Object> annotations, String kbId, String reviewerId,
                                     Integer expectedSnapshotVersion,
                                     Map<String, Object> effects) {
        if (status != PipelineStatus.completed) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "知识审阅关在会话 completed 后裁决（当前 " + status + "）");
        }
        IrSnapshotEntity ir5 = latestSnapshot(sessionId, "IR5");
        if (ir5 == null) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "缺少 IR5 快照");
        }
        assertExpectedVersion("IR5", expectedSnapshotVersion, ir5.getVersion());
        // C-0（T15.6）：knowledge 关组合凭证（IR5 版本+人工选定目标库 kbId）随裁决入档
        String metadataJson = knowledgeMetadata(ir5, kbId);
        String wbStatus = readJsonField(ir5.getContent(), "writeback_status");
        if ("rejected".equals(decision)) {
            // 跳过=显式终态：IR5 pending→skipped（幂等校验在此，重复点击 409，不再堆裁决记录）
            if (!"pending".equals(wbStatus)) {
                throw new ResponseStatusException(HttpStatus.CONFLICT,
                        "IR5 回写状态为 " + wbStatus + "，无需重复跳过");
            }
            Map<String, Object> auto = new LinkedHashMap<>();
            auto.put("reviewed_ir", "IR5 v" + ir5.getVersion());
            try {
                writebackClient.skip(sessionId);
                auto.put("writeback", "skipped（人工跳过，不回写）");
                effects.put("writeback", "skipped");
            } catch (Exception exc) {
                log.error("知识回写跳过失败 session={}——IR5 保持 pending 可重试", sessionId, exc);
                auto.put("writeback", "failed: " + exc.getMessage());
                effects.put("writeback", "failed");
            }
            ReviewRecordEntity record = insertRecord(sessionId, "knowledge", ir5.getId(), decision,
                    annotations, reviewerId, auto, metadataJson);
            effects.put("record_id", record.getId());
            return;
        }
        // approved：pending 或已跳过（补做）均可；同步调用 py-engine 回写；失败如实记录可重试
        if (!"pending".equals(wbStatus) && !"skipped".equals(wbStatus)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "IR5 回写状态为 " + wbStatus + "，无可回写候选");
        }
        Map<String, Object> auto = new LinkedHashMap<>();
        auto.put("reviewed_ir", "IR5 v" + ir5.getVersion());
        try {
            Map<String, Object> result = writebackClient.writeback(sessionId, kbId);
            auto.put("writeback", writebackSummary(result));
            effects.put("writeback", result);
        } catch (Exception exc) {
            log.error("知识回写失败 session={}——决策已记录，IR5 保持 pending", sessionId, exc);
            auto.put("writeback", "failed: " + exc.getMessage());
            effects.put("writeback", "failed");
        }
        ReviewRecordEntity record = insertRecord(sessionId, "knowledge", ir5.getId(), decision,
                annotations, reviewerId, auto, metadataJson);
        effects.put("record_id", record.getId());
    }

    /** 回写结果 Map → 摘要字符串入库（整对象入库曾致前端批注显示 [object Object]）。 */
    private String writebackSummary(Map<String, Object> result) {
        StringBuilder sb = new StringBuilder("已写入图谱");
        List<String> parts = new java.util.ArrayList<>();
        if (result.get("written") instanceof Map<?, ?> w) {
            Object n = w.get("written");
            Object d = w.get("deprecated");
            if (n != null) parts.add("新增 " + n);
            if (d != null) parts.add("废弃 " + d);
        }
        if (result.get("ir_version") != null) parts.add("IR5 v" + result.get("ir_version"));
        if (!parts.isEmpty()) sb.append("（").append(String.join(" · ", parts)).append("）");
        return sb.toString();
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
                                            String reviewerId, Map<String, Object> autoFields,
                                            String metadataJson) {
        ReviewRecordEntity record = new ReviewRecordEntity();
        record.setSessionId(sessionId);
        record.setGate(gate);
        record.setIrSnapshotId(irSnapshotId);
        record.setDecision(decision);
        record.setReviewerId(reviewerId);
        record.setDecidedAt(OffsetDateTime.now());
        String syntheticTraceId = "rv-" + UUID.randomUUID().toString().substring(0, 8);
        Map<String, Object> annotationPayload = new LinkedHashMap<>();
        annotationPayload.put("annotations", annotations == null ? List.of() : annotations);
        annotationPayload.put("trace_id", syntheticTraceId);
        annotationPayload.putAll(autoFields);
        record.setAnnotations(toJson(annotationPayload));
        if (metadataJson != null) {
            record.setMetadata(metadataJson);
        }
        reviewRecordMapper.insert(record);
        // 溯源链 confirm 行（T2.4）：裁决以合成 trace_id（rv- 前缀）可反查
        TraceChainEntity trace = new TraceChainEntity();
        trace.setTraceId(syntheticTraceId);
        trace.setStepNo(1);
        trace.setSessionId(sessionId);
        trace.setActor("review");
        trace.setAction("confirm");
        trace.setIrSnapshotId(irSnapshotId);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("gate", gate);
        payload.put("decision", decision);
        payload.put("reviewer", reviewerId);
        trace.setPayload(toJson(payload));
        traceChainMapper.insert(trace);
        return record;
    }

    public List<ReviewRecordEntity> list(String sessionId) {
        return reviewRecordMapper.selectList(new LambdaQueryWrapper<ReviewRecordEntity>()
                .eq(ReviewRecordEntity::getSessionId, sessionId)
                .orderByDesc(ReviewRecordEntity::getDecidedAt));
    }

    /** reviewer UUID(归一化去横线小写) → username（裁决历史展示实际操作者，2026-09-20 用户反馈）。 */
    public Map<String, String> reviewerNames(List<ReviewRecordEntity> records) {
        List<String> ids = records.stream()
                .map(ReviewRecordEntity::getReviewerId)
                .filter(id -> id != null && id.matches("[0-9a-fA-F-]{32,36}"))
                .distinct()
                .toList();
        if (ids.isEmpty()) return Map.of();
        Map<String, String> byId = new LinkedHashMap<>();
        for (UserLookupEntity r : userLookupMapper.selectList(
                new LambdaQueryWrapper<UserLookupEntity>()
                        .in(UserLookupEntity::getId, ids)
                        .select(UserLookupEntity::getId, UserLookupEntity::getUsername))) {
            byId.put(r.getId().replace("-", "").toLowerCase(), r.getUsername());
        }
        return byId;
    }

    // ------------------------------------------------------------------
    // T15.6 / C-0 批准版本组合（ADR-014 §5）：gate2 批准时固化 IR2/IR3/IR4 最新有效版组合
    // 与政策（ir4_overall / 人工例外 / 未决项）进 review_records.metadata（V20 jsonb）；
    // knowledge 关记 {ir5_version, kb_id}。历史行 metadata NULL=组合契约前记录，诚实可辨。
    // ------------------------------------------------------------------

    /** gate2 组合固化结果：ir3 兼作被审主快照（被审对象=组合内版本，二者永不分叉）。 */
    private record Gate2Combo(IrSnapshotEntity ir3, String metadataJson) {}

    private Gate2Combo buildGate2Combo(String sessionId, Boolean exceptionGranted,
                                       String exceptionReason, Integer expectedIr4Version) {
        IrSnapshotEntity ir2 = latestValidSnapshot(sessionId, "IR2");
        IrSnapshotEntity ir3 = latestValidSnapshot(sessionId, "IR3");
        IrSnapshotEntity ir4 = latestValidSnapshot(sessionId, "IR4");
        List<String> missing = new ArrayList<>();
        if (ir2 == null) missing.add("IR2");
        if (ir3 == null) missing.add("IR3");
        if (ir4 == null) missing.add("IR4");
        if (!missing.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "gate2 批准要求批准组合三件套齐备（各取最新有效版），缺少: "
                            + String.join("、", missing));
        }
        // 并发条件扩展（ADR-014 §5 修订 3）：IR4 版本条件——防批准冻结进过期校验报告
        assertExpectedVersion("IR4", expectedIr4Version, ir4.getVersion());

        String overall = readJsonField(ir4.getContent(), "overall");
        boolean fail = "fail".equals(overall);
        if (Boolean.TRUE.equals(exceptionGranted) && !fail) {
            throw new IllegalArgumentException(
                    "仅 IR4 overall=fail 的批准允许携带人工例外（当前 overall=" + overall + "）");
        }
        Map<String, Object> exception = null;
        if (fail) {
            if (!Boolean.TRUE.equals(exceptionGranted)
                    || exceptionReason == null || exceptionReason.isBlank()) {
                throw new ResponseStatusException(HttpStatus.CONFLICT,
                        "IR4 overall=fail 的批准必须携带显式人工例外（exceptionGranted=true + 非空 "
                                + "exceptionReason）——gate2 批准 ≠ 几何全合格，不得静默放行（ADR-014 §5）");
            }
            exception = new LinkedHashMap<>();
            exception.put("granted", true);
            exception.put("reason", exceptionReason);
        }

        OffsetDateTime validAt = OffsetDateTime.now();
        Map<String, Object> combo = new LinkedHashMap<>();
        combo.put("IR2", comboEntry(ir2, validAt));
        combo.put("IR3", comboEntry(ir3, validAt));
        combo.put("IR4", comboEntry(ir4, validAt));

        Map<String, Object> policy = new LinkedHashMap<>();
        policy.put("ir4_overall", overall == null ? "unknown" : overall);
        policy.put("exception", exception);
        policy.put("unresolved", collectUnresolved(ir4, latestValidSnapshot(sessionId, "IR1")));

        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("contract", "ADR-014§5");
        metadata.put("policy_version", "1");
        metadata.put("snapshot_combo", combo);
        metadata.put("combo_policy", policy);
        return new Gate2Combo(ir3, toJson(metadata));
    }

    /** 组合条目。content_sha256 口径 = sha256(PG jsonb 规范文本 content::text)——pgjdbc getString
     *  直出与 py-engine 侧 SELECT content::text 同源字节，跨语言复现（ADR-014 §5 修订 1），
     *  C-1 导出按此校验「内容与批准时一致」。 */
    private Map<String, Object> comboEntry(IrSnapshotEntity snap, OffsetDateTime validAt) {
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("snapshot_id", snap.getId());
        entry.put("version", snap.getVersion());
        entry.put("content_sha256", sha256Hex(snap.getContent() == null ? "" : snap.getContent()));
        entry.put("valid_at_decision", validAt.toString());
        return entry;
    }

    /** 未决项入档（ADR-014 §5）：IR4 unverified 族（constraint_id）+ IR1 missing_items——
     *  incomplete 批准放行但应检未检显性留档；C-1 正式导出按此渲染未决项章。 */
    private List<Map<String, String>> collectUnresolved(IrSnapshotEntity ir4, IrSnapshotEntity ir1) {
        List<Map<String, String>> unresolved = new ArrayList<>();
        try {
            var root = JSON.readTree(ir4.getContent() == null ? "{}" : ir4.getContent());
            for (var issue : root.path("issues")) {
                if ("unverified".equals(issue.path("severity").asText())) {
                    String ref = issue.path("constraint_id").asText("");
                    if (ref.isBlank()) {
                        ref = "issues[" + unresolved.size() + "] " + issue.path("message").asText("");
                    }
                    unresolved.add(Map.of("source", "IR4", "ref", ref));
                }
            }
        } catch (Exception e) {
            log.warn("C-0 未决项收集：IR4 content 解析失败 session={}（如实跳过，不影响批准）",
                    ir4.getSessionId(), e);
        }
        if (ir1 != null) {
            try {
                var root = JSON.readTree(ir1.getContent());
                for (var item : root.path("missing_items")) {
                    unresolved.add(Map.of("source", "IR1", "ref", item.asText()));
                }
            } catch (Exception e) {
                log.warn("C-0 未决项收集：IR1 content 解析失败（如实跳过）", e);
            }
        }
        return unresolved;
    }

    /** knowledge 关组合凭证：IR5 版本 + 人工选定目标库 kbId（此前 kbId 仅入请求不入记录，T15.6 补齐）。 */
    private String knowledgeMetadata(IrSnapshotEntity ir5, String kbId) {
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("ir5_snapshot_id", ir5.getId());
        metadata.put("ir5_version", ir5.getVersion());
        metadata.put("kb_id", kbId);
        return toJson(metadata);
    }

    /** 最新【有效】版本（invalidated_at IS NULL）：组合只冻结有效快照。不过滤的 latestSnapshot
     *  语义保留给驳回目标推断等既有路径（审阅/前端经 workflow API 仍可查全部历史）。 */
    private IrSnapshotEntity latestValidSnapshot(String sessionId, String irType) {
        return irSnapshotMapper.selectOne(new LambdaQueryWrapper<IrSnapshotEntity>()
                .eq(IrSnapshotEntity::getSessionId, sessionId)
                .eq(IrSnapshotEntity::getIrType, irType)
                .isNull(IrSnapshotEntity::getInvalidatedAt)
                .orderByDesc(IrSnapshotEntity::getVersion)
                .last("LIMIT 1"));
    }

    private static String sha256Hex(String text) {
        try {
            java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
            StringBuilder sb = new StringBuilder(64);
            for (byte b : md.digest(text.getBytes(java.nio.charset.StandardCharsets.UTF_8))) {
                sb.append(Character.forDigit((b >> 4) & 0xF, 16))
                        .append(Character.forDigit(b & 0xF, 16));
            }
            return sb.toString();
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
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
