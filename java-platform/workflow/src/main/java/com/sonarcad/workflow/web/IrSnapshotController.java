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
 *   GET /api/v1/workflow/sessions/{sid}/irs/{type}/versions/{version}  按版本内容（T15.6/C-2 读侧配套）
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
                "invalidated", snapshot.getInvalidatedAt() != null,
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
                "invalidated", r.getInvalidatedAt() != null,
                "created_at", String.valueOf(r.getCreatedAt()))).toList());
    }

    /** T15.6/C-2 读侧配套：按精确版本取快照内容（批准组合卡全参数 diff 等只读视图用）。
     *  与 latest 端点同构；历史版本本就是不可变审计对象（A-12），失效版本仍可读（invalidated 标注，
     *  正式导出的有效性裁决在服务端导出链，不在此处）。 */
    @GetMapping("/{type}/versions/{version}")
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<Map<String, Object>> version(@PathVariable String sid, @PathVariable String type,
                                                    @PathVariable int version) {
        String irType = normalize(type);
        IrSnapshotEntity snapshot = irSnapshotMapper.selectOne(
                new LambdaQueryWrapper<IrSnapshotEntity>()
                        .eq(IrSnapshotEntity::getSessionId, sid)
                        .eq(IrSnapshotEntity::getIrType, irType)
                        .eq(IrSnapshotEntity::getVersion, version));
        if (snapshot == null) {
            throw new IllegalArgumentException("无快照: session=" + sid + " ir=" + irType + " v" + version);
        }
        return ApiResponse.ok(Map.of(
                "ir_type", irType,
                "version", snapshot.getVersion(),
                "schema_version", snapshot.getSchemaVersion(),
                "created_by", snapshot.getCreatedBy() == null ? "" : snapshot.getCreatedBy(),
                "invalidated", snapshot.getInvalidatedAt() != null,
                "content", snapshot.getContent()));
    }

    private String normalize(String type) {
        String t = type.toUpperCase();
        if (!t.matches("IR[0-5]")) throw new IllegalArgumentException("非法 IR 类型: " + type);
        return t;
    }
}
