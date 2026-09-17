package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.workflow.mq.TaskPublisher;
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
 */
@Service
public class SessionService {

    private final DesignSessionMapper mapper;
    private final IrSnapshotMapper irSnapshotMapper;
    private final TaskPublisher taskPublisher;

    public SessionService(DesignSessionMapper mapper, IrSnapshotMapper irSnapshotMapper, TaskPublisher taskPublisher) {
        this.mapper = mapper;
        this.irSnapshotMapper = irSnapshotMapper;
        this.taskPublisher = taskPublisher;
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
        // START 成功后投递首个管线任务（T1.3 起：A1 需求解析；echo 保留为 /debug 联通性任务）
        if (event == PipelineEvent.START) {
            taskPublisher.publishA1Task(id);
        }
        s.setPipelineStatus(target.name());
        return s;
    }

    public Map<String, Object> allowedEvents(String id) {
        return Map.of("events", PipelineTransitions.allowedEvents(
                PipelineStatus.valueOf(get(id).getPipelineStatus())));
    }
}
