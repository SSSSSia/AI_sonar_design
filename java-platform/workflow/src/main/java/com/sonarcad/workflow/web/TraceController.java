package com.sonarcad.workflow.web;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sonarcad.workflow.session.IrSnapshotEntity;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.session.TraceChainEntity;
import com.sonarcad.workflow.session.TraceChainMapper;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 溯源链反查（T2.4，宪法 §5.4 基线端点）：
 *   GET /api/v1/trace/{trace_id}
 *
 * 返回链路步骤（step_no 升序，A2 derive → A3 transform → A4 validate → A5 assign /
 * review confirm）+ 上下文视图（IR0 需求原文、IR1 目标与检索引用、IR2 参数全貌）——
 * 任一参数从 IR2 反查到 IR0 原文与检索引用（E2 验收判据）。
 */
@RestController
@RequestMapping("/api/v1/trace")
public class TraceController {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final TraceChainMapper traceChainMapper;
    private final IrSnapshotMapper irSnapshotMapper;

    public TraceController(TraceChainMapper traceChainMapper, IrSnapshotMapper irSnapshotMapper) {
        this.traceChainMapper = traceChainMapper;
        this.irSnapshotMapper = irSnapshotMapper;
    }

    @GetMapping("/{traceId}")
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<Map<String, Object>> trace(@PathVariable String traceId) {
        List<TraceChainEntity> rows = traceChainMapper.selectList(
                new LambdaQueryWrapper<TraceChainEntity>()
                        .eq(TraceChainEntity::getTraceId, traceId)
                        .orderByAsc(TraceChainEntity::getStepNo));
        if (rows.isEmpty()) {
            throw new IllegalArgumentException("无溯源记录: " + traceId);
        }
        String sessionId = rows.get(0).getSessionId();

        List<Map<String, Object>> steps = new ArrayList<>();
        for (TraceChainEntity r : rows) {
            Map<String, Object> step = new LinkedHashMap<>();
            step.put("step_no", r.getStepNo());
            step.put("actor", r.getActor());
            step.put("action", r.getAction());
            step.put("ir_snapshot_id", r.getIrSnapshotId());
            step.put("payload", parse(r.getPayload()));
            step.put("created_at", String.valueOf(r.getCreatedAt()));
            steps.add(step);
        }

        Map<String, Object> context = new LinkedHashMap<>();
        context.put("session_id", sessionId);
        Map<String, Object> ir0 = latestContent(sessionId, "IR0");
        if (ir0 != null) {
            context.put("ir0_raw_text", ir0.get("raw_text"));
        }
        Map<String, Object> ir1 = latestContent(sessionId, "IR1");
        if (ir1 != null) {
            context.put("ir1_objectives", ir1.get("objectives"));
            context.put("ir1_retrieved_refs", ir1.get("retrieved_context"));
        }
        Map<String, Object> ir2 = latestContent(sessionId, "IR2");
        if (ir2 != null) {
            context.put("ir2_param", findParam(ir2, traceId));
        }

        return ApiResponse.ok(Map.of("trace_id", traceId, "steps", steps, "context", context));
    }

    private Map<String, Object> findParam(Map<String, Object> ir2, String traceId) {
        Object params = ir2.get("parameters");
        if (params instanceof List<?> list) {
            for (Object o : list) {
                if (o instanceof Map<?, ?> p && traceId.equals(String.valueOf(p.get("trace_id")))) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> found = (Map<String, Object>) p;
                    return found;
                }
            }
        }
        return null;
    }

    private Map<String, Object> latestContent(String sessionId, String irType) {
        IrSnapshotEntity snap = irSnapshotMapper.selectOne(
                new LambdaQueryWrapper<IrSnapshotEntity>()
                        .eq(IrSnapshotEntity::getSessionId, sessionId)
                        .eq(IrSnapshotEntity::getIrType, irType)
                        .orderByDesc(IrSnapshotEntity::getVersion)
                        .last("LIMIT 1"));
        return snap == null ? null : parse(snap.getContent());
    }

    private Map<String, Object> parse(String json) {
        try {
            return JSON.readValue(json == null ? "{}" : json, Map.class);
        } catch (Exception e) {
            return Map.of();
        }
    }
}
