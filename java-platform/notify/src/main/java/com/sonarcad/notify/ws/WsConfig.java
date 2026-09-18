package com.sonarcad.notify.ws;

import com.sonarcad.notify.security.JwtVerifier;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.http.server.ServletServerHttpRequest;
import org.springframework.lang.NonNull;
import org.springframework.web.socket.WebSocketHandler;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;
import org.springframework.web.socket.server.HandshakeInterceptor;

import java.util.Map;

@Configuration
@EnableWebSocket
public class WsConfig implements WebSocketConfigurer {

    private final SessionWebSocketHandler handler;
    private final JwtVerifier jwtVerifier;

    public WsConfig(SessionWebSocketHandler handler, JwtVerifier jwtVerifier) {
        this.handler = handler;
        this.jwtVerifier = jwtVerifier;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        // 生产经 nginx /ws/ 前缀转发（保持路径不变）；开发经 vite /ws 代理，两者都保留 /ws 前缀。
        // 验签必须在握手拦截器做：处理器层拒连发生在 101 升级之后，客户端已视为连上。
        registry.addHandler(handler, "/ws/api/v1/ws/sessions/*")
                .addInterceptors(new TokenHandshakeInterceptor(jwtVerifier))
                .setAllowedOriginPatterns("*");
    }

    /** ?token=JWT 验签失败 → 握手直接拒绝（403/401，客户端建连 future 异常）。 */
    static final class TokenHandshakeInterceptor implements HandshakeInterceptor {

        private final JwtVerifier jwtVerifier;

        TokenHandshakeInterceptor(JwtVerifier jwtVerifier) {
            this.jwtVerifier = jwtVerifier;
        }

        @Override
        public boolean beforeHandshake(@NonNull ServerHttpRequest request, @NonNull ServerHttpResponse response,
                                       @NonNull WebSocketHandler wsHandler, @NonNull Map<String, Object> attributes) {
            String token = null;
            if (request instanceof ServletServerHttpRequest servlet) {
                token = servlet.getServletRequest().getParameter("token");
            }
            try {
                attributes.put("sub", jwtVerifier.verify(token));
                return true;
            } catch (Exception e) {
                response.setStatusCode(org.springframework.http.HttpStatus.UNAUTHORIZED);
                return false;
            }
        }

        @Override
        public void afterHandshake(@NonNull ServerHttpRequest request, @NonNull ServerHttpResponse response,
                                   @NonNull WebSocketHandler wsHandler, Exception exception) {
            // 无需处理
        }
    }
}
