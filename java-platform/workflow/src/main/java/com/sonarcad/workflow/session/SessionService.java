package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.workflow.mq.TaskPublisher;
import com.sonarcad.workflow.notify.NotifyClient;
import com.sonarcad.workflow.notify.NotifyMessages;
import com.sonarcad.workflow.statemachine.PipelineEvent;
import com.sonarcad.workflow.statemachine.PipelineStatus;
import com.sonarcad.workflow.statemachine.PipelineTransitions;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.time.OffsetDateTime;
import java.util.Map;

/**
 * 会话服务：状态转移按 PipelineTransitions 裁决后落库（乐观 where 条件防并发双写）。
 * T1.7 起 START 事件同时投递 MQ 任务给 py-engine（本期先落状态）。
 * T4.1 起关键转移同时向 notify 发布 WS 进度/审阅请求（失败仅告警，推送是增强不是权威）。
 */
@Service
public class SessionService {

    private final DesignSessionMapper mapper;
    private final IrSnapshotMapper irSnapshotMapper;
    private final TaskPublisher taskPublisher;
    private final NotifyClient notifyClient;
    private final UserLookupMapper userLookupMapper;
    private final ProjectLookupMapper projectLookupMapper;
    private final KbBaseLookupMapper kbBaseLookupMapper;
    private final CadTuneLookupMapper cadTuneLookupMapper;

    public SessionService(DesignSessionMapper mapper, IrSnapshotMapper irSnapshotMapper,
                          TaskPublisher taskPublisher, NotifyClient notifyClient,
                          UserLookupMapper userLookupMapper, ProjectLookupMapper projectLookupMapper,
                          KbBaseLookupMapper kbBaseLookupMapper, CadTuneLookupMapper cadTuneLookupMapper) {
        this.mapper = mapper;
        this.irSnapshotMapper = irSnapshotMapper;
        this.taskPublisher = taskPublisher;
        this.notifyClient = notifyClient;
        this.userLookupMapper = userLookupMapper;
        this.projectLookupMapper = projectLookupMapper;
        this.kbBaseLookupMapper = kbBaseLookupMapper;
        this.cadTuneLookupMapper = cadTuneLookupMapper;
    }

    /**
     * 创建会话（pending）。requirement 非空时固化为 IR0 快照 v1（created_by=user），
     * 供 A1 消费（A-04：需求原文一经创建不可变，修订 = 新会话或 IR0 新快照）。
     * kbIds 非空时逐个校验存在（PHASE-006 D6-6）；null/空 = Agent 自主决策模式。
     */
    public DesignSessionEntity create(String projectId, String title, String requirement,
                                      java.util.List<String> kbIds, String createdBy) {
        DesignSessionEntity s = new DesignSessionEntity();
        s.setProjectId(projectId);
        s.setTitle(title);
        s.setPipelineStatus(PipelineStatus.pending.name());
        s.setCreatedBy(createdBy);
        if (kbIds != null && !kbIds.isEmpty()) {
            s.setKbIds(serializeKbIds(validateKbIds(kbIds)));
        }
        mapper.insert(s);
        if (requirement != null && !requirement.isBlank()) {
            IrSnapshotEntity ir0 = new IrSnapshotEntity();
            ir0.setSessionId(s.getId());
            ir0.setIrType("IR0");
            ir0.setVersion(1);
            ir0.setSchemaVersion("v1");
            ir0.setContent(serializeIr0(requirement));
            ir0.setCreatedBy("user");
            irSnapshotMapper.insert(ir0);
        }
        if (kbIds != null && !kbIds.isEmpty()) {
            // 创建响应直接带库名（前端抽屉/toast 即显，不等二次查询）
            s.setKbNames(validateKbIds(kbIds).stream()
                    .map(KbBaseLookupEntity::getName)
                    .collect(java.util.stream.Collectors.toList()));
        }
        return s;
    }

    /** PHASE-006 D6-6：无效 kb_id 创建时报 400（优于运行时静默零命中）。uuid 列格式预检防 DB 500。 */
    private java.util.List<KbBaseLookupEntity> validateKbIds(java.util.List<String> kbIds) {
        java.util.LinkedHashSet<String> distinct = new java.util.LinkedHashSet<>(kbIds);
        for (String id : distinct) {
            if (!id.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "知识库不存在: " + id);
            }
        }
        java.util.List<KbBaseLookupEntity> found = kbBaseLookupMapper.selectList(
                new LambdaQueryWrapper<KbBaseLookupEntity>().in(KbBaseLookupEntity::getId, distinct));
        java.util.Set<String> foundIds = found.stream()
                .map(KbBaseLookupEntity::getId)
                .collect(java.util.stream.Collectors.toSet());
        for (String id : distinct) {
            if (!foundIds.contains(id)) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "知识库不存在: " + id);
            }
        }
        // 保持去重后的请求顺序
        java.util.List<KbBaseLookupEntity> ordered = new java.util.ArrayList<>();
        for (String id : distinct) {
            found.stream().filter(r -> r.getId().equals(id)).findFirst().ifPresent(ordered::add);
        }
        return ordered;
    }

    private String serializeKbIds(java.util.List<KbBaseLookupEntity> bases) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < bases.size(); i++) {
            if (i > 0) sb.append(",");
            sb.append('"').append(bases.get(i).getId()).append('"');
        }
        return sb.append("]").toString();
    }

    private String serializeIr0(String requirement) {
        try {
            return new com.fasterxml.jackson.databind.ObjectMapper()
                    .writeValueAsString(java.util.Map.of("raw_text", requirement));
        } catch (Exception e) {
            throw new IllegalStateException("IR0 序列化失败", e);
        }
    }

    public DesignSessionEntity get(String id) {
        DesignSessionEntity s = mapper.selectById(id);
        if (s == null) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "会话不存在: " + id);
        fillCreatorNames(java.util.List.of(s));
        fillProjectNames(java.util.List.of(s));
        fillKbNames(java.util.List.of(s));
        fillTuneStatus(java.util.List.of(s));
        return s;
    }

    public Page<DesignSessionEntity> list(String projectId, long page, long size) {
        Page<DesignSessionEntity> p = mapper.selectPage(new Page<>(page, size),
                new LambdaQueryWrapper<DesignSessionEntity>()
                        .eq(projectId != null, DesignSessionEntity::getProjectId, projectId)
                        .orderByDesc(DesignSessionEntity::getCreatedAt));
        fillCreatorNames(p.getRecords());
        fillProjectNames(p.getRecords());
        fillKbNames(p.getRecords());
        fillTuneStatus(p.getRecords());
        return p;
    }

    /** kb_ids JSON 数组 → 库名列表（kb_bases 同库只读回填，与 fillProjectNames 同模式；PHASE-006）。 */
    private void fillKbNames(java.util.List<DesignSessionEntity> sessions) {
        java.util.Set<String> ids = new java.util.HashSet<>();
        for (DesignSessionEntity s : sessions) {
            ids.addAll(parseKbIds(s.getKbIds()));
        }
        if (ids.isEmpty()) return;
        java.util.List<KbBaseLookupEntity> rows = kbBaseLookupMapper.selectList(
                new LambdaQueryWrapper<KbBaseLookupEntity>()
                        .in(KbBaseLookupEntity::getId, ids)
                        .select(KbBaseLookupEntity::getId, KbBaseLookupEntity::getName));
        java.util.Map<String, String> byId = new java.util.HashMap<>();
        for (KbBaseLookupEntity r : rows) {
            byId.put(r.getId(), r.getName());
        }
        for (DesignSessionEntity s : sessions) {
            java.util.List<String> parsed = parseKbIds(s.getKbIds());
            if (parsed.isEmpty()) continue;
            java.util.List<String> names = new java.util.ArrayList<>();
            for (String id : parsed) {
                String name = byId.get(id);
                if (name != null) names.add(name);
            }
            if (!names.isEmpty()) s.setKbNames(names);
        }
    }

    /** 活跃微调会话回填（PHASE-008 T8.6）：cad_tune_sessions 同库只读，会话详情/列表带 tune 状态。
     *  仅回填 active（审阅警示只关心进行中）；design_session_id 列读回带横线，与归一化 id 对齐后匹配。 */
    private void fillTuneStatus(java.util.List<DesignSessionEntity> sessions) {
        java.util.Set<String> ids = sessions.stream()
                .map(DesignSessionEntity::getId)
                .filter(id -> id != null && !id.isBlank())
                .collect(java.util.stream.Collectors.toSet());
        if (ids.isEmpty()) return;
        java.util.List<CadTuneLookupEntity> rows = cadTuneLookupMapper.selectList(
                new LambdaQueryWrapper<CadTuneLookupEntity>()
                        .in(CadTuneLookupEntity::getDesignSessionId, ids)
                        .eq(CadTuneLookupEntity::getStatus, "active"));
        if (rows.isEmpty()) return;
        java.util.Map<String, CadTuneLookupEntity> bySid = new java.util.HashMap<>();
        for (CadTuneLookupEntity r : rows) {
            bySid.put(r.getDesignSessionId().replace("-", ""), r);
        }
        for (DesignSessionEntity s : sessions) {
            CadTuneLookupEntity t = bySid.get(s.getId());
            if (t != null) {
                s.setTuneId(t.getId());
                s.setTuneStatus(t.getStatus());
            }
        }
    }

    /** kb_ids JSON 数组字符串容错解析（null/坏 JSON/非数组 → 空列表，D-05 如实不炸）。 */
    public static java.util.List<String> parseKbIds(String kbIds) {
        if (kbIds == null || kbIds.isBlank()) return java.util.List.of();
        try {
            com.fasterxml.jackson.databind.JsonNode node =
                    new com.fasterxml.jackson.databind.ObjectMapper().readTree(kbIds);
            java.util.List<String> out = new java.util.ArrayList<>();
            if (node.isArray()) {
                for (com.fasterxml.jackson.databind.JsonNode n : node) {
                    if (n.isTextual() && !n.asText().isBlank()) out.add(n.asText());
                }
            }
            return out;
        } catch (Exception e) {
            return java.util.List.of();
        }
    }

    /** project_id → 项目名（projects 由 biz 维护、同库只读；删除悬空时留空，前端回退短 id）。 */
    private void fillProjectNames(java.util.List<DesignSessionEntity> sessions) {
        java.util.Set<String> ids = sessions.stream()
                .map(DesignSessionEntity::getProjectId)
                .filter(id -> id != null && !id.isBlank())
                .collect(java.util.stream.Collectors.toSet());
        if (ids.isEmpty()) return;
        java.util.List<ProjectLookupEntity> rows = projectLookupMapper.selectList(
                new LambdaQueryWrapper<ProjectLookupEntity>()
                        .in(ProjectLookupEntity::getId, ids)
                        .select(ProjectLookupEntity::getId, ProjectLookupEntity::getName));
        java.util.Map<String, String> byId = new java.util.HashMap<>();
        for (ProjectLookupEntity r : rows) {
            byId.put(r.getId(), r.getName());
        }
        for (DesignSessionEntity s : sessions) {
            if (s.getProjectId() == null) continue;
            String name = byId.get(s.getProjectId());
            if (name != null) s.setProjectName(name);
        }
    }

    /** created_by UUID → username（审查 P1-2：界面不再裸吐 UUID）。users 与本服务同库，直接查。 */
    private void fillCreatorNames(java.util.List<DesignSessionEntity> sessions) {
        java.util.Set<String> ids = sessions.stream()
                .map(DesignSessionEntity::getCreatedBy)
                .filter(id -> id != null && id.matches("[0-9a-fA-F-]{32,36}"))
                .collect(java.util.stream.Collectors.toSet());
        if (ids.isEmpty()) return;
        java.util.List<UserLookupEntity> rows = userLookupMapper.selectList(
                new LambdaQueryWrapper<UserLookupEntity>()
                        .in(UserLookupEntity::getId, ids)
                        .select(UserLookupEntity::getId, UserLookupEntity::getUsername));
        java.util.Map<String, String> byId = new java.util.HashMap<>();
        for (UserLookupEntity r : rows) {
            byId.put(r.getId().replace("-", "").toLowerCase(), r.getUsername());
        }
        for (DesignSessionEntity s : sessions) {
            if (s.getCreatedBy() == null) continue;
            String name = byId.get(s.getCreatedBy().replace("-", "").toLowerCase());
            if (name != null) s.setCreatedByName(name);
        }
    }

    public DesignSessionEntity fireEvent(String id, PipelineEvent event) {
        DesignSessionEntity s = get(id);

        // STAGE_DONE：worker 中间阶段完成回调——固定管线续派（A-01/A-05），不改会话状态。
        // worker 落快照时先写 current_stage 再回调，故此处 stage 即"刚完成的 Agent"。
        // T2.2 审阅关接线：A2 后暂停 gate1、A4 后暂停 gate2（每份新产物都须人工过闸，PHASE-002 §2.1）。
        if (event == PipelineEvent.STAGE_DONE) {
            String stage = s.getCurrentStage();
            if ("A1".equals(stage)) {
                taskPublisher.publishA2Task(id);
                notifyClient.publish(id, NotifyMessages.taskProgress("A2", "running",
                        NotifyMessages.stageProgress("A2"), "A2 方案生成中"));
            } else if ("A2".equals(stage)) {
                pauseAtGate(id, "gate1");
            } else if ("A3".equals(stage)) {
                taskPublisher.publishA4Task(id);
                notifyClient.publish(id, NotifyMessages.taskProgress("A4", "running",
                        NotifyMessages.stageProgress("A4"), "A4 合规校验中"));
            } else if ("A4".equals(stage)) {
                pauseAtGate(id, "gate2");
            }
            return s;
        }

        // RETRY（阶段 5 验收反馈）：失败会话按 current_stage 重派；转移前先校验阶段可派发，
        // 避免"先转 running 再发现派不了"的脏状态。
        if (event == PipelineEvent.RETRY) {
            String stage = s.getCurrentStage() == null ? "" : s.getCurrentStage();
            if (!stage.matches("A[1-5]")) {
                throw new ResponseStatusException(HttpStatus.CONFLICT,
                        "失败会话无可重试阶段（current_stage=" + stage + "）");
            }
        }

        PipelineStatus current = PipelineStatus.valueOf(s.getPipelineStatus());
        PipelineStatus target = PipelineTransitions.targetOf(current, event);
        if (target == null) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "非法状态转移: " + current + " --" + event + "--> （允许: " + PipelineTransitions.allowedEvents(current) + "）");
        }
        DesignSessionEntity patch = new DesignSessionEntity();
        patch.setPipelineStatus(target.name());
        // START 即标记 A1（启动空窗修复）：不等 worker 接单回写，前端时间线/阶段列从第一屏就有值
        if (event == PipelineEvent.START) patch.setCurrentStage("A1");
        int updated = mapper.update(patch, new LambdaQueryWrapper<DesignSessionEntity>()
                .eq(DesignSessionEntity::getId, id)
                .eq(DesignSessionEntity::getPipelineStatus, current.name()));
        if (updated == 0) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "状态已被并发修改，请重试");
        }
        // START 成功后投递首个管线任务（A1 需求解析；echo 保留为 /debug 联通性任务）
        if (event == PipelineEvent.START) {
            s.setCurrentStage("A1");
            taskPublisher.publishA1Task(id);
            notifyClient.publish(id, NotifyMessages.taskProgress("A1", "running",
                    NotifyMessages.stageProgress("A1"), "A1 需求解析已派发"));
        } else if (event == PipelineEvent.COMPLETE || event == PipelineEvent.FAIL) {
            String stage = s.getCurrentStage() == null ? "A5" : s.getCurrentStage();
            notifyClient.publish(id, NotifyMessages.taskProgress(NotifyMessages.contractStage(stage),
                    event == PipelineEvent.COMPLETE ? "completed" : "failed",
                    event == PipelineEvent.COMPLETE ? 100 : NotifyMessages.stageProgress(stage), null));
        } else if (event == PipelineEvent.PAUSE) {
            String stage = s.getCurrentStage() == null ? "A1" : s.getCurrentStage();
            notifyClient.publish(id, NotifyMessages.taskProgress(NotifyMessages.contractStage(stage),
                    "paused", NotifyMessages.stageProgress(stage), "已手动暂停"));
        } else if (event == PipelineEvent.RETRY) {
            String stage = s.getCurrentStage();
            switch (stage) {
                case "A1" -> taskPublisher.publishA1Task(id);
                case "A2" -> taskPublisher.publishA2Task(id);
                case "A3" -> taskPublisher.publishA3Task(id);
                case "A4" -> taskPublisher.publishA4Task(id);
                case "A5" -> taskPublisher.publishA5Task(id);
                default -> throw new IllegalStateException("未知重试阶段: " + stage);
            }
            notifyClient.publish(id, NotifyMessages.taskProgress(NotifyMessages.contractStage(stage), "running",
                    NotifyMessages.stageProgress(stage), "失败重试：重新派发 " + stage));
        }
        s.setPipelineStatus(target.name());
        return s;
    }

    public Map<String, Object> allowedEvents(String id) {
        return Map.of("events", PipelineTransitions.allowedEvents(
                PipelineStatus.valueOf(get(id).getPipelineStatus())));
    }

    /**
     * 审阅关暂停（T2.2）：running → paused + current_stage=gate，单次乐观更新。
     * 复用现有 paused/PAUSE 语义（无新增状态/事件）；恢复由审阅裁决驱动（ReviewService）。
     */
    public DesignSessionEntity pauseAtGate(String id, String gate) {
        DesignSessionEntity patch = new DesignSessionEntity();
        patch.setPipelineStatus(PipelineStatus.paused.name());
        patch.setCurrentStage(gate);
        int updated = mapper.update(patch, new LambdaQueryWrapper<DesignSessionEntity>()
                .eq(DesignSessionEntity::getId, id)
                .eq(DesignSessionEntity::getPipelineStatus, PipelineStatus.running.name()));
        if (updated == 0) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "审阅关暂停失败：会话不在 running 状态（可能已被并发修改）");
        }
        DesignSessionEntity s = get(id);
        s.setPipelineStatus(PipelineStatus.paused.name());
        s.setCurrentStage(gate);
        String irType = "gate1".equals(gate) ? "IR2" : "IR3";
        notifyClient.publish(id, NotifyMessages.taskProgress(NotifyMessages.contractStage(gate), "paused",
                NotifyMessages.stageProgress(gate), "等待人工裁决"));
        notifyClient.publish(id, NotifyMessages.reviewRequest(gate, irType,
                latestVersion(id, irType), s.getTitle()));
        return s;
    }

    /** 审阅关恢复（T2.2）：paused → running + current_stage=下一棒，乐观更新（并发双裁决在此收敛 409）。 */
    public void resumeFromGate(String id, String nextStage) {
        DesignSessionEntity patch = new DesignSessionEntity();
        patch.setPipelineStatus(PipelineStatus.running.name());
        patch.setCurrentStage(nextStage);
        int updated = mapper.update(patch, new LambdaQueryWrapper<DesignSessionEntity>()
                .eq(DesignSessionEntity::getId, id)
                .eq(DesignSessionEntity::getPipelineStatus, PipelineStatus.paused.name()));
        if (updated == 0) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "审阅关恢复失败：会话不在 paused 状态（可能已被并发裁决）");
        }
        notifyClient.publish(id, NotifyMessages.taskProgress(nextStage, "running",
                NotifyMessages.stageProgress(nextStage), null));
    }

    private String latestVersion(String sessionId, String irType) {
        IrSnapshotEntity latest = irSnapshotMapper.selectList(new LambdaQueryWrapper<IrSnapshotEntity>()
                        .eq(IrSnapshotEntity::getSessionId, sessionId)
                        .eq(IrSnapshotEntity::getIrType, irType)
                        .orderByDesc(IrSnapshotEntity::getVersion)
                        .last("LIMIT 1"))
                .stream().findFirst().orElse(null);
        return latest == null ? "0" : String.valueOf(latest.getVersion());
    }
}
