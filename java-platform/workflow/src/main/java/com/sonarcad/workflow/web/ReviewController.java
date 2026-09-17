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
        List<Map<String, Object>> rows = reviewService.list(sid).stream().map(r -> Map.<String, Object>of(
                "id", r.getId(),
                "gate", r.getGate(),
                "decision", r.getDecision(),
                "ir_snapshot_id", r.getIrSnapshotId() == null ? "" : r.getIrSnapshotId(),
                "annotations", r.getAnnotations() == null ? "" : r.getAnnotations(),
                "reviewer_id", r.getReviewerId() == null ? "" : r.getReviewerId(),
                "decided_at", String.valueOf(r.getDecidedAt()))).toList();
        return ApiResponse.ok(rows);
    }
}
