package com.sonarcad.workflow.web;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sonarcad.workflow.session.IrSnapshotEntity;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * IR 快照查询（T1.7 补齐宪法 §5.4 基线端点）：
 *   GET /api/v1/workflow/sessions/{sid}/irs/{type}            最新版本内容
 *   GET /api/v1/workflow/sessions/{sid}/irs/{type}/versions   版本列表（元数据）
 */
@RestController
@RequestMapping("/api/v1/workflow/sessions/{sid}/irs")
public class IrSnapshotController {

    private final IrSnapshotMapper irSnapshotMapper;

    public IrSnapshotController(IrSnapshotMapper irSnapshotMapper) {
        this.irSnapshotMapper = irSnapshotMapper;
    }

    @GetMapping("/{type}")
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<Map<String, Object>> latest(@PathVariable String sid, @PathVariable String type) {
        String irType = normalize(type);
        IrSnapshotEntity snapshot = irSnapshotMapper.selectOne(
                new LambdaQueryWrapper<IrSnapshotEntity>()
                        .eq(IrSnapshotEntity::getSessionId, sid)
                        .eq(IrSnapshotEntity::getIrType, irType)
                        .orderByDesc(IrSnapshotEntity::getVersion)
                        .last("LIMIT 1"));
        if (snapshot == null) {
            throw new IllegalArgumentException("无快照: session=" + sid + " ir=" + irType);
        }
        return ApiResponse.ok(Map.of(
                "ir_type", irType,
                "version", snapshot.getVersion(),
                "schema_version", snapshot.getSchemaVersion(),
                "created_by", snapshot.getCreatedBy() == null ? "" : snapshot.getCreatedBy(),
                "content", snapshot.getContent()));
    }

    @GetMapping("/{type}/versions")
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<List<Map<String, Object>>> versions(@PathVariable String sid, @PathVariable String type) {
        String irType = normalize(type);
        List<IrSnapshotEntity> rows = irSnapshotMapper.selectList(
                new LambdaQueryWrapper<IrSnapshotEntity>()
                        .eq(IrSnapshotEntity::getSessionId, sid)
                        .eq(IrSnapshotEntity::getIrType, irType)
                        .orderByDesc(IrSnapshotEntity::getVersion));
        return ApiResponse.ok(rows.stream().map(r -> Map.<String, Object>of(
                "version", r.getVersion(),
                "schema_version", r.getSchemaVersion(),
                "created_by", r.getCreatedBy() == null ? "" : r.getCreatedBy(),
                "created_at", String.valueOf(r.getCreatedAt()))).toList());
    }

    private String normalize(String type) {
        String t = type.toUpperCase();
        if (!t.matches("IR[0-5]")) throw new IllegalArgumentException("非法 IR 类型: " + type);
        return t;
    }
}
