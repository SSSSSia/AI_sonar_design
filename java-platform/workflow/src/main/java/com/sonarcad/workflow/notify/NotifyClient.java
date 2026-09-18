package com.sonarcad.workflow.notify;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * notify 推送客户端（T4.1）：把 WS 信封发到 notify 内部发布端点。
 * 推送是增强不是权威——失败仅告警，进度可由前端轮询/对账收敛（与溯源链写入同 degrade 哲学）。
 * 强制 HTTP/1.1：JDK HttpClient 默认 h2c Upgrade 头会被某些服务端丢 body（阶段 2 教训）。
 */
@Component
public class NotifyClient {

    private static final Logger log = LoggerFactory.getLogger(NotifyClient.class);

    private final HttpClient http = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(2))
            .build();
    private final ObjectMapper mapper = new ObjectMapper();
    private final String baseUrl;

    public NotifyClient(@Value("${notify.base-url:http://localhost:8084}") String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public void publish(String sessionId, Map<String, Object> envelope) {
        try {
            HttpRequest request = HttpRequest.newBuilder(
                            URI.create(baseUrl + "/internal/sessions/" + sessionId + "/messages"))
                    .header("Content-Type", "application/json")
                    .header("X-User-Id", "workflow-service")
                    .timeout(Duration.ofSeconds(3))
                    .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(envelope)))
                    .build();
            http.sendAsync(request, HttpResponse.BodyHandlers.discarding())
                    .orTimeout(3, TimeUnit.SECONDS)
                    .whenComplete((resp, ex) -> {
                        if (ex != null) {
                            log.warn("notify 发布失败 session={}（不影响管线）: {}", sessionId, ex.getMessage());
                        } else if (resp.statusCode() >= 300) {
                            log.warn("notify 发布被拒 session={} status={}", sessionId, resp.statusCode());
                        }
                    });
        } catch (Exception e) {
            log.warn("notify 发布构造失败 session={}（不影响管线）", sessionId, e);
        }
    }
}
