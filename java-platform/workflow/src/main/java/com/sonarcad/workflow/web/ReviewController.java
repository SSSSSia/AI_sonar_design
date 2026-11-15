package com.sonarcad.workflow.web;

import com.sonarcad.workflow.audit.AuditRecorder;
import com.sonarcad.workflow.session.ReviewRecordEntity;
import com.sonarcad.workflow.session.ReviewService;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 审阅关裁决 API（T2.2，宪法 §5.4 基线）：
 *   POST /api/v1/workflow/sessions/{sid}/reviews/{gate}   gate ∈ gate1|gate2|knowledge
 *   GET  /api/v1/workflow/sessions/{sid}/reviews          裁决历史
 *
 * 裁决权限：gate:decide 在角色层落实（JWT 只带角色）——admin/reviewer，与 V2 种子的
 * 角色-权限矩阵一致（designer/viewer 403）。T13.1：裁决动作落审计（§9.4「人工审阅决定」），
 * 批注内容仍由 review_records 承担，审计 detail 只记裁决摘要。
 */
@RestController
@RequestMapping("/api/v1/workflow/sessions/{sid}/reviews")
public class ReviewController {

    private final ReviewService reviewService;
    private final AuditRecorder auditRecorder;

    public ReviewController(ReviewService reviewService, AuditRecorder auditRecorder) {
        this.reviewService = reviewService;
        this.auditRecorder = auditRecorder;
    }

    /**
     * T14.6（外部审查问题 4c）：expectedSnapshotVersion = 审阅人实际查看的快照版本——
     * 与裁决时刻最新版本不符时 409 SNAPSHOT_STALE（旧页面不能批准新模型）。
     * T15.6/C-0（ADR-014 §5）：exceptionGranted/exceptionReason = IR4 overall=fail 时的显式人工例外
     * （必填且仅 fail 可携带）；expectedIr4Version = IR4 并发条件（防批准冻结进过期校验报告）。
     * 全部可选字段：缺省（旧客户端/脚本）保持现行为——但 fail 静默批准自此被 409 拒绝（政策收口）。
     */
    public record DecideRequest(String decision, List<Object> annotations, String reworkStage,
                                String kbId, Integer expectedSnapshotVersion,
                                Boolean exceptionGranted, String exceptionReason,
                                Integer expectedIr4Version) {
    }

    @PostMapping("/{gate}")
    @PreAuthorize("hasAnyRole('admin','reviewer')")
    public ApiResponse<Map<String, Object>> decide(@PathVariable String sid,
                                                   @PathVariable String gate,
                                                   @RequestBody DecideRequest req,
                                                   @RequestHeader(value = "X-User-Id", required = false) String reviewerId) {
        if (req == null || req.decision() == null || req.decision().isBlank()) {
            throw new IllegalArgumentException("decision 不能为空（approved|rejected）");
        }
        Map<String, Object> result = reviewService.decide(
                sid, gate, req.decision(), req.annotations(), req.reworkStage(), req.kbId(),
                reviewerId, req.expectedSnapshotVersion(),
                req.exceptionGranted(), req.exceptionReason(), req.expectedIr4Version());
        Map<String, Object> detail = new java.util.LinkedHashMap<>();
        detail.put("decision", req.decision());
        if (req.reworkStage() != null) {
            detail.put("rework_stage", req.reworkStage());
        }
        detail.put("annotation_count", req.annotations() == null ? 0 : req.annotations().size());
        auditRecorder.record(reviewerId, "review.decide", "session", sid, detail, null);
        return ApiResponse.ok(result);
    }

    @GetMapping
    @PreAuthorize("hasAnyRole('admin','designer','reviewer','viewer')")
    public ApiResponse<List<Map<String, Object>>> list(@PathVariable String sid) {
        List<ReviewRecordEntity> records = reviewService.list(sid);
        Map<String, String> names = reviewService.reviewerNames(records);
        List<Map<String, Object>> rows = records.stream().map(r -> {
            String reviewerId = r.getReviewerId() == null ? "" : r.getReviewerId();
            String reviewerName = names.get(reviewerId.replace("-", "").toLowerCase());
            Map<String, Object> row = new java.util.LinkedHashMap<>();
            row.put("id", r.getId());
            row.put("gate", r.getGate());
            row.put("decision", r.getDecision());
            row.put("ir_snapshot_id", r.getIrSnapshotId() == null ? "" : r.getIrSnapshotId());
            row.put("annotations", r.getAnnotations() == null ? "" : r.getAnnotations());
            // T15.6/C-0：批准组合凭证原样透出（jsonb 字符串，前端解析；NULL=组合契约前记录）
            row.put("metadata", r.getMetadata() == null ? "" : r.getMetadata());
            row.put("reviewer_id", reviewerId);
            row.put("reviewer_name", reviewerName == null ? "" : reviewerName);
            row.put("decided_at", String.valueOf(r.getDecidedAt()));
            return row;
        }).toList();
        return ApiResponse.ok(rows);
    }
}
