package com.sonarcad.notify;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * WebSocket 推送服务：任务进度、日志、审阅请求通知。
 * 消息格式必须符合 contracts/ws/ 下的 schema（宪法 §5.5），禁止裸字符串协议。
 */
@SpringBootApplication
public class NotifyApplication {

    public static void main(String[] args) {
        SpringApplication.run(NotifyApplication.class, args);
    }
}
