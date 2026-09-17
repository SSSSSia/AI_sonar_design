package com.sonarcad.workflow.web;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.Map;

/**
 * py-engine 知识回写客户端（T2.3）：知识审阅关 approved 后同步调用
 * POST /knowledge/writeback（内网直连）。失败抛 RestClientException，由
 * ReviewService 捕获并如实记入裁决记录——决策已落库，IR5 保持 pending 可重试（A-16）。
 */
@Component
public class KnowledgeWritebackClient {

    private final RestClient restClient;

    public KnowledgeWritebackClient(
            @Value("${py-engine.base-url:http://localhost:8090}") String baseUrl) {
        this.restClient = RestClient.builder().baseUrl(baseUrl).build();
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> writeback(String sessionId) {
        return restClient.post()
                .uri("/knowledge/writeback")
                .contentType(MediaType.APPLICATION_JSON)
                .body(Map.of("session_id", sessionId))
                .retrieve()
                .body(Map.class);
    }
}
