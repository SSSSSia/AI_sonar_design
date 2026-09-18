package com.sonarcad.notify;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpResponse;
import java.net.http.WebSocket;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * T4.1 notify 集成测试：WS 验签建连、内部发布、信任头与契约校验。
 * 推送链路的发布方（workflow/py-engine）各自在自己模块验证容错，本处只测 hub 本体。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class NotifyIntegrationTest {

    @Autowired
    TestRestTemplate rest;

    static final String SECRET = "test-secret-notify";

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("jwt.secret", () -> SECRET);
    }

    // ---- helpers ----

    private static String b64url(byte[] bytes) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private static String makeJwt() throws Exception {
        String header = b64url("{\"alg\":\"HS512\"}".getBytes(StandardCharsets.UTF_8));
        long exp = System.currentTimeMillis() / 1000 + 600;
        String payload = b64url(("{\"sub\":\"user-1\",\"exp\":" + exp + "}").getBytes(StandardCharsets.UTF_8));
        Mac mac = Mac.getInstance("HmacSHA512");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA512"));
        String signingInput = header + "." + payload;
        String sig = b64url(mac.doFinal(signingInput.getBytes(StandardCharsets.US_ASCII)));
        return signingInput + "." + sig;
    }

    /** JDK 自带 WS 客户端：记录 open 与首条消息。 */
    private static final class Listener implements WebSocket.Listener {
        final CompletableFuture<Void> open = new CompletableFuture<>();
        final CompletableFuture<String> firstMessage = new CompletableFuture<>();

        @Override
        public void onOpen(WebSocket webSocket) {
            open.complete(null);
            WebSocket.Listener.super.onOpen(webSocket);
        }

        @Override
        public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
            firstMessage.complete(data.toString());
            return WebSocket.Listener.super.onText(webSocket, data, last);
        }
    }

    private HttpClient http() {
        return HttpClient.newHttpClient();
    }

    private String wsUrl(int port, String sid, String token) {
        return "ws://localhost:" + port + "/ws/api/v1/ws/sessions/" + sid
                + (token == null ? "" : "?token=" + token);
    }

    // ---- tests ----

    @org.junit.jupiter.api.Test
    void wsConnectWithoutTokenIsRejected(@org.springframework.boot.test.web.server.LocalServerPort int port) {
        CompletableFuture<WebSocket> f = http().newWebSocketBuilder()
                .buildAsync(URI.create(wsUrl(port, "sess-x", null)), new Listener());
        org.assertj.core.api.Assertions.assertThatThrownBy(f::join).hasCauseInstanceOf(Exception.class);
    }

    @org.junit.jupiter.api.Test
    void wsConnectWithBadSignatureIsRejected(@org.springframework.boot.test.web.server.LocalServerPort int port) {
        CompletableFuture<WebSocket> f = http().newWebSocketBuilder()
                .buildAsync(URI.create(wsUrl(port, "sess-x", "a.b.c")), new Listener());
        org.assertj.core.api.Assertions.assertThatThrownBy(f::join).hasCauseInstanceOf(Exception.class);
    }

    @org.junit.jupiter.api.Test
    void publishRequiresTrustHeader(@org.springframework.boot.test.web.server.LocalServerPort int port) throws Exception {
        // 用 JDK HttpClient：TestRestTemplate 对 401 响应在流式 body 下会重试报 I/O error
        HttpResponse<String> resp = http().send(java.net.http.HttpRequest.newBuilder()
                        .uri(URI.create("http://localhost:" + port + "/internal/sessions/sess-x/messages"))
                        .header("Content-Type", "application/json")
                        .POST(java.net.http.HttpRequest.BodyPublishers.ofString(
                                "{\"type\":\"task_progress\",\"payload\":{\"stage\":\"A1\",\"status\":\"running\",\"progress\":10}}"))
                        .build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(resp.statusCode()).isEqualTo(401);
    }

    @org.junit.jupiter.api.Test
    void publishRejectsUnknownType(@org.springframework.boot.test.web.server.LocalServerPort int port) {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-User-Id", "it-test");
        ResponseEntity<String> resp = rest.exchange(
                "http://localhost:" + port + "/internal/sessions/sess-x/messages",
                HttpMethod.POST, new HttpEntity<>(Map.of("type", "bogus", "payload", Map.of()), headers), String.class);
        assertThat(resp.getStatusCode().value()).isEqualTo(400);
    }

    @org.junit.jupiter.api.Test
    void publishDeliversEnvelopeToSubscriber(@org.springframework.boot.test.web.server.LocalServerPort int port) throws Exception {
        Listener listener = new Listener();
        WebSocket ws = http().newWebSocketBuilder()
                .buildAsync(URI.create(wsUrl(port, "sess-it-1", makeJwt())), listener)
                .get(5, TimeUnit.SECONDS);
        listener.open.get(5, TimeUnit.SECONDS);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.set("X-User-Id", "it-test");
        Map<String, Object> envelope = Map.of(
                "type", "task_progress",
                "payload", Map.of("stage", "A1", "status", "running", "progress", 10, "message", "已派发"));

        // 客户端 onOpen 可能先于服务端 afterConnectionEstablished 登记——轮询直到订阅生效
        ResponseEntity<String> resp = null;
        for (int i = 0; i < 15; i++) {
            resp = rest.exchange(
                    "http://localhost:" + port + "/internal/sessions/sess-it-1/messages",
                    HttpMethod.POST, new HttpEntity<>(envelope, headers), String.class);
            if (resp.getBody() != null && resp.getBody().contains("\"subscribers\":1")) {
                break;
            }
            Thread.sleep(200);
        }
        assertThat(resp).isNotNull();
        assertThat(resp.getStatusCode().value()).isEqualTo(200);
        assertThat(resp.getBody()).contains("\"subscribers\":1");

        String received = listener.firstMessage.get(5, TimeUnit.SECONDS);
        assertThat(received).contains("task_progress").contains("sess-it-1").contains("\"progress\":10");

        HttpResponse<String> httpResp = http().send(java.net.http.HttpRequest.newBuilder()
                        .uri(URI.create("http://localhost:" + port + "/internal/sessions/sess-it-1/messages"))
                        .header("Content-Type", "application/json")
                        .header("X-User-Id", "it-test")
                        .POST(java.net.http.HttpRequest.BodyPublishers.ofString("{\"type\":\"ir_updated\",\"payload\":{}}"))
                        .build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(httpResp.statusCode()).isEqualTo(200);
        ws.sendClose(WebSocket.NORMAL_CLOSURE, "done");
    }
}
