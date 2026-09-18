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

    public SessionService(DesignSessionMapper mapper, IrSnapshotMapper irSnapshotMapper,
                          TaskPublisher taskPublisher, NotifyClient notifyClient) {
        this.mapper = mapper;
        this.irSnapshotMapper = irSnapshotMapper;
        this.taskPublisher = taskPublisher;
        this.notifyClient = notifyClient;
    }

    /**
     * 创建会话（pending）。requirement 非空时固化为 IR0 快照 v1（created_by=user），
     * 供 A1 消费（A-04：需求原文一经创建不可变，修订 = 新会话或 IR0 新快照）。
     */
    public DesignSessionEntity create(String projectId, String title, String requirement, String createdBy) {
        DesignSessionEntity s = new DesignSessionEntity();
        s.setProjectId(projectId);
        s.setTitle(title);
        s.setPipelineStatus(PipelineStatus.pending.name());
        s.setCreatedBy(createdBy);
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
        return s;
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
        return s;
    }

    public Page<DesignSessionEntity> list(String projectId, long page, long size) {
        return mapper.selectPage(new Page<>(page, size),
                new LambdaQueryWrapper<DesignSessionEntity>()
                        .eq(projectId != null, DesignSessionEntity::getProjectId, projectId)
                        .orderByDesc(DesignSessionEntity::getCreatedAt));
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

        PipelineStatus current = PipelineStatus.valueOf(s.getPipelineStatus());
        PipelineStatus target = PipelineTransitions.targetOf(current, event);
        if (target == null) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "非法状态转移: " + current + " --" + event + "--> （允许: " + PipelineTransitions.allowedEvents(current) + "）");
        }
        DesignSessionEntity patch = new DesignSessionEntity();
        patch.setPipelineStatus(target.name());
        int updated = mapper.update(patch, new LambdaQueryWrapper<DesignSessionEntity>()
                .eq(DesignSessionEntity::getId, id)
                .eq(DesignSessionEntity::getPipelineStatus, current.name()));
        if (updated == 0) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "状态已被并发修改，请重试");
        }
        // START 成功后投递首个管线任务（A1 需求解析；echo 保留为 /debug 联通性任务）
        if (event == PipelineEvent.START) {
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
