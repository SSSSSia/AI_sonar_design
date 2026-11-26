package com.sonarcad.workflow.session;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 会话参考图片附件引用（T18.5，PHASE-018；ADR-017）。
 * IR 引用惯例 {bucket, object_key[, sha256]}（P3 §8.3：IR 不携带大对象）；
 * object_key 为 sha256 前 16 位内容寻址形态（py-engine 上传端点产出），
 * bucket 白名单 session-attachments（控制器校验，防注入任意桶）。
 */
public record AttachmentRef(
        String bucket,
        @JsonProperty("object_key") String objectKey,
        String sha256) {
}
