package com.sonarcad.notify.ws;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sonarcad.notify.security.JwtVerifier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.io.IOException;
import java.net.URI;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArraySet;

/**
 * 会话频道 WS hub：/ws/api/v1/ws/sessions/{sid}?token=JWT（消息信封见 contracts/ws/）。
 * 内存订阅表即可——推送是增强而非权威（进度可由 HTTP 对账收敛），不做持久化订阅。
 */
@Component
public class SessionWebSocketHandler extends TextWebSocketHandler {

    private static final Logger log = LoggerFactory.getLogger(SessionWebSocketHandler.class);
    private static final String PATH_PREFIX = "/ws/api/v1/ws/sessions/";

    private final ConcurrentHashMap<String, Set<WebSocketSession>> subscribers = new ConcurrentHashMap<>();
    private final ObjectMapper mapper = new ObjectMapper();

    public SessionWebSocketHandler() {
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        // 验签已在 TokenHandshakeInterceptor（握手前）完成，此处只登记订阅
        String sid = sidOf(session);
        subscribers.computeIfAbsent(sid, k -> new CopyOnWriteArraySet<>()).add(session);
        log.debug("WS 订阅 sid={}（当前 {} 个订阅者）", sid, subscribers.get(sid).size());
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        Set<WebSocketSession> set = subscribers.get(sidOf(session));
        if (set != null) {
            set.remove(session);
        }
    }

    /** 向会话频道广播；返回实际送达的订阅者数。单 session 加锁发送，避免并发写帧。 */
    public int broadcast(String sid, Object envelope) {
        String json;
        try {
            json = mapper.writeValueAsString(envelope);
        } catch (IOException e) {
            throw new IllegalStateException("WS 消息序列化失败", e);
        }
        Set<WebSocketSession> set = subscribers.get(sid);
        if (set == null || set.isEmpty()) {
            return 0;
        }
        int delivered = 0;
        for (WebSocketSession ws : set) {
            if (!ws.isOpen()) {
                set.remove(ws);
                continue;
            }
            try {
                synchronized (ws) {
                    ws.sendMessage(new TextMessage(json));
                }
                delivered++;
            } catch (IOException e) {
                set.remove(ws);
                log.debug("WS 发送失败，移除订阅者：{}", e.getMessage());
            }
        }
        return delivered;
    }

    /** 向全部存活会话频道广播（T10.1c 告警通道：监控告警无会话归属，到达所有在连客户端）。返回送达数。 */
    public int broadcastAll(Object envelope) {
        String json;
        try {
            json = mapper.writeValueAsString(envelope);
        } catch (IOException e) {
            throw new IllegalStateException("WS 消息序列化失败", e);
        }
        int delivered = 0;
        for (Set<WebSocketSession> set : subscribers.values()) {
            for (WebSocketSession ws : set) {
                if (!ws.isOpen()) {
                    set.remove(ws);
                    continue;
                }
                try {
                    synchronized (ws) {
                        ws.sendMessage(new TextMessage(json));
                    }
                    delivered++;
                } catch (IOException e) {
                    set.remove(ws);
                    log.debug("WS 全播发送失败，移除订阅者：{}", e.getMessage());
                }
            }
        }
        return delivered;
    }

    public int subscriberCount(String sid) {
        Set<WebSocketSession> set = subscribers.get(sid);
        return set == null ? 0 : set.size();
    }

    private String sidOf(WebSocketSession session) {
        URI uri = session.getUri();
        String path = uri == null ? "" : uri.getPath();
        if (!path.startsWith(PATH_PREFIX) || path.length() <= PATH_PREFIX.length()) {
            throw new IllegalArgumentException("非法 WS 路径: " + path);
        }
        return path.substring(PATH_PREFIX.length());
    }
}
