package com.sonarcad.workflow.web;

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
 * 角色-权限矩阵一致（designer/viewer 403）。
 */
@RestController
@RequestMapping("/api/v1/workflow/sessions/{sid}/reviews")
public class ReviewController {

    private final ReviewService reviewService;

    public ReviewController(ReviewService reviewService) {
        this.reviewService = reviewService;
    }

    public record DecideRequest(String decision, List<Object> annotations, String reworkStage) {
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
        return ApiResponse.ok(reviewService.decide(
                sid, gate, req.decision(), req.annotations(), req.reworkStage(), reviewerId));
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
            row.put("reviewer_id", reviewerId);
            row.put("reviewer_name", reviewerName == null ? "" : reviewerName);
            row.put("decided_at", String.valueOf(r.getDecidedAt()));
            return row;
        }).toList();
        return ApiResponse.ok(rows);
    }
}
