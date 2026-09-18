package com.sonarcad.workflow.notify;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * WS 信封工厂（结构对齐 contracts/ws/ws-message.schema.json，禁止裸字符串协议）。
 * stage→progress 为粗粒度里程碑：A1:10 / A2:25 / gate1:45 / A3:55 / A4:70 / gate2:85 / A5:92 / 完成:100。
 */
public final class NotifyMessages {

    private NotifyMessages() {
    }

    public static int stageProgress(String stage) {
        return switch (stage) {
            case "A1" -> 10;
            case "A2" -> 25;
            case "gate1" -> 45;
            case "A3" -> 55;
            case "A4" -> 70;
            case "gate2" -> 85;
            case "A5" -> 92;
            default -> 100;
        };
    }

    /** 审阅关 current_stage（gate1/gate2）→ 契约 stage 枚举（review_gate_1/review_gate_2）。 */
    public static String contractStage(String currentStage) {
        return switch (currentStage) {
            case "gate1" -> "review_gate_1";
            case "gate2" -> "review_gate_2";
            default -> currentStage;
        };
    }

    public static Map<String, Object> taskProgress(String stage, String status, int progress, String message) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("stage", stage);
        payload.put("status", status);
        payload.put("progress", progress);
        if (message != null) {
            payload.put("message", message);
        }
        return envelope("task_progress", payload);
    }

    public static Map<String, Object> reviewRequest(String gate, String irType, String irVersion, String summary) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("gate", gate);
        payload.put("ir_type", irType);
        payload.put("ir_version", irVersion);
        if (summary != null) {
            payload.put("summary", summary);
        }
        return envelope("review_request", payload);
    }

    private static Map<String, Object> envelope(String type, Map<String, Object> payload) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("type", type);
        envelope.put("timestamp", OffsetDateTime.now(ZoneOffset.UTC).toString());
        envelope.put("payload", payload);
        return envelope;
    }
}
