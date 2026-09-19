package com.sonarcad.workflow.session;

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
 * py-engine CAD 产物清理客户端（会话删除联动）：DELETE /cad/sessions/{sid}/artifacts。
 * 与 KnowledgeWritebackClient 同款约束：强制 HTTP/1.1（h2c Upgrade 会丢 body）；
 * 失败只记结果不阻断——存储残留可重删，会话行的删除是权威。
 */
@Component
public class CadArtifactsClient {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final String baseUrl;
    private final HttpClient httpClient = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    public CadArtifactsClient(
            @Value("${py-engine.base-url:http://localhost:8090}") String baseUrl) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
    }

    /** 尽力而为：成功返回计数 Map，失败返回错误说明（调用方如实透出，不抛）。 */
    @SuppressWarnings("unchecked")
    public Map<String, Object> purgeQuietly(String sessionId) {
        try {
            HttpRequest request = HttpRequest.newBuilder(
                            URI.create(baseUrl + "/api/v1/cad/sessions/" + sessionId + "/artifacts"))
                    .header("X-User-Id", "system-cleanup")
                    .timeout(Duration.ofSeconds(15))
                    .DELETE()
                    .build();
            HttpResponse<String> resp = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() >= 300) {
                return Map.of("error", "HTTP " + resp.statusCode() + ": " + resp.body());
            }
            return JSON.readValue(resp.body(), Map.class);
        } catch (Exception e) {
            if (e instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            return Map.of("error", String.valueOf(e.getMessage()));
        }
    }
}
