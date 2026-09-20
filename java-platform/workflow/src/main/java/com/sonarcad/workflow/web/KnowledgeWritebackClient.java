package com.sonarcad.workflow.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

/**
 * py-engine 知识回写客户端（T2.3）：知识审阅关 approved 后同步调用
 * POST /knowledge/writeback（内网直连）。失败抛异常，由
 * ReviewService 捕获并如实记入裁决记录——决策已落库，IR5 保持 pending 可重试（A-16）。
 *
 * 实现注意：强制 HTTP/1.1——JDK HttpClient 默认带 h2c Upgrade 头，uvicorn/h11
 * 会把这类请求的 body 弄丢（实机 422：FastAPI 收到空 body）；HTTP_1_1 后行为正常。
 */
@Component
public class KnowledgeWritebackClient {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final String baseUrl;
    private final HttpClient httpClient = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    public KnowledgeWritebackClient(
            @Value("${py-engine.base-url:http://localhost:8090}") String baseUrl) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> writeback(String sessionId, String kbId) {
        String body;
        try {
            java.util.Map<String, Object> payload = new java.util.LinkedHashMap<>();
            payload.put("session_id", sessionId);
            if (kbId != null && !kbId.isBlank()) {
                payload.put("kb_id", kbId);  // PHASE-006 D6-8：人工选定的目标库
            }
            body = JSON.writeValueAsString(payload);
            HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + "/knowledge/writeback"))
                    .header("Content-Type", "application/json")
                    .timeout(Duration.ofSeconds(25))
                    .POST(HttpRequest.BodyPublishers.ofString(body))
                    .build();
            HttpResponse<String> resp = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() >= 300) {
                throw new IllegalStateException("writeback HTTP " + resp.statusCode() + ": " + resp.body());
            }
            return JSON.readValue(resp.body(), Map.class);
        } catch (java.io.IOException | InterruptedException e) {
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            throw new IllegalStateException("writeback 调用失败: " + e.getMessage(), e);
        }
    }
}
