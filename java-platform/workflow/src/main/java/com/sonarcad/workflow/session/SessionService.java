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
    private final TaskPublisher taskPublisher;

    public SessionService(DesignSessionMapper mapper, TaskPublisher taskPublisher) {
        this.mapper = mapper;
        this.taskPublisher = taskPublisher;
    }

    public DesignSessionEntity create(String projectId, String title, String createdBy) {
        DesignSessionEntity s = new DesignSessionEntity();
        s.setProjectId(projectId);
        s.setTitle(title);
        s.setPipelineStatus(PipelineStatus.pending.name());
        s.setCreatedBy(createdBy);
        mapper.insert(s);
        return s;
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
        // START 成功后投递首个任务（T1.2 骨架：echo 验证任务；T1.3 起换 A1 真实任务）
        if (event == PipelineEvent.START) {
            taskPublisher.publishEchoTask(id);
        }
        s.setPipelineStatus(target.name());
        return s;
    }

    public Map<String, Object> allowedEvents(String id) {
        return Map.of("events", PipelineTransitions.allowedEvents(
                PipelineStatus.valueOf(get(id).getPipelineStatus())));
    }
}
