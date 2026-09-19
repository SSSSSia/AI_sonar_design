package com.sonarcad.workflow.session;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sonarcad.workflow.statemachine.PipelineStatus;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 会话删除（2026-09-19 用户验收需求）：
 *   终态（pending/completed/failed）直接删；运行中/暂停须 force=true（任务可能仍在途，
 *   ir_snapshots 的外键会让迟到写入直接失败——不留孤儿行，任务报错属如实）。
 * 级联：ir_snapshots / review_records / trace_chains 三表 + MinIO CAD 产物（尽力而为）。
 * Neo4j 已过审沉淀的知识**保留**（共享资产；source_session 悬空属如实，不连坐删知识）。
 */
@Service
public class SessionCleanupService {

    private final DesignSessionMapper sessionMapper;
    private final IrSnapshotMapper irSnapshotMapper;
    private final ReviewRecordMapper reviewRecordMapper;
    private final TraceChainMapper traceChainMapper;
    private final CadArtifactsClient cadArtifactsClient;

    public SessionCleanupService(DesignSessionMapper sessionMapper, IrSnapshotMapper irSnapshotMapper,
                                 ReviewRecordMapper reviewRecordMapper, TraceChainMapper traceChainMapper,
                                 CadArtifactsClient cadArtifactsClient) {
        this.sessionMapper = sessionMapper;
        this.irSnapshotMapper = irSnapshotMapper;
        this.reviewRecordMapper = reviewRecordMapper;
        this.traceChainMapper = traceChainMapper;
        this.cadArtifactsClient = cadArtifactsClient;
    }

    @Transactional
    public Map<String, Object> delete(String id, boolean force) {
        DesignSessionEntity s = sessionMapper.selectById(id);
        if (s == null) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "会话不存在: " + id);
        }
        PipelineStatus status = PipelineStatus.valueOf(s.getPipelineStatus());
        if ((status == PipelineStatus.running || status == PipelineStatus.paused) && !force) {
            throw new ResponseStatusException(HttpStatus.CONFLICT,
                    "会话未结束（当前 " + status + "）：任务可能仍在执行，确认要删请带 force=true");
        }

        int ir = irSnapshotMapper.delete(new LambdaQueryWrapper<IrSnapshotEntity>()
                .eq(IrSnapshotEntity::getSessionId, id));
        int reviews = reviewRecordMapper.delete(new LambdaQueryWrapper<ReviewRecordEntity>()
                .eq(ReviewRecordEntity::getSessionId, id));
        int traces = traceChainMapper.delete(new LambdaQueryWrapper<TraceChainEntity>()
                .eq(TraceChainEntity::getSessionId, id));
        int removed = sessionMapper.deleteById(id);
        if (removed == 0) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "会话已被并发删除");
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("deleted", true);
        result.put("session_id", id);
        result.put("ir_snapshots", ir);
        result.put("review_records", reviews);
        result.put("trace_chain_rows", traces);
        result.put("minio", cadArtifactsClient.purgeQuietly(id));
        result.put("neo4j_note", "已过审沉淀的知识默认保留（共享资产），其 source_session 来源标记随之悬空");
        return result;
    }
}
