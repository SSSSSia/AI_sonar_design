package com.sonarcad.workflow.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.core.MessagePostProcessor;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.core.TopicExchange;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 任务投递（T1.2，D-05/D-06）：编排层在会话 START 时把任务发到 MQ，
 * py-engine Celery worker 消费。编排层只投递与回收状态，不做算力（A-05）。
 *
 * 消息格式 = Celery protocol v2：
 *   headers: {task, id, lang, retries}；content_type=application/json；
 *   body = [args, kwargs, {callbacks:null, errbacks:null, chain:null, chord:null}]
 * 交换机 sonarcad.task（topic，持久化）；队列与死信声明由 py-engine 侧负责（单一声明方）。
 */
@Service
public class TaskPublisher {

    private static final Logger log = LoggerFactory.getLogger(TaskPublisher.class);
    public static final String EXCHANGE = "sonarcad.task";

    private final RabbitTemplate rabbitTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public TaskPublisher(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    public void publishEchoTask(String sessionId) {
        publish("sonarcad.echo", "task.echo", List.of(sessionId, "echo:" + sessionId), Map.of());
    }

    /** A1 需求解析任务（T1.3）：参数只有 sessionId，IR 由 worker 从共享存储读取。 */
    public void publishA1Task(String sessionId) {
        publish("sonarcad.a1", "task.a1", List.of(sessionId), Map.of());
    }

    /** A2 方案生成任务（T1.4） */
    public void publishA2Task(String sessionId) {
        publish("sonarcad.a2", "task.a2", List.of(sessionId), Map.of());
    }

    /** A3 CAD 建模任务（T1.5） */
    public void publishA3Task(String sessionId) {
        publish("sonarcad.a3", "task.a3", List.of(sessionId), Map.of());
    }

    public void publish(String taskName, String routingKey, List<Object> args, Map<String, Object> kwargs) {
        String taskId = UUID.randomUUID().toString();
        try {
            // embed 字段（callbacks/errbacks/chain/chord）按协议为 null，Map.of 不接受 null，用 HashMap
            Map<String, Object> embed = new java.util.HashMap<>();
            embed.put("callbacks", null);
            embed.put("errbacks", null);
            embed.put("chain", null);
            embed.put("chord", null);
            String body = objectMapper.writeValueAsString(List.of(args, kwargs, embed));
            Message message = new Message(body.getBytes(StandardCharsets.UTF_8), new MessageProperties());
            message.getMessageProperties().setContentType(MessageProperties.CONTENT_TYPE_JSON);
            message.getMessageProperties().setContentEncoding(StandardCharsets.UTF_8.name());
            message.getMessageProperties().setDeliveryMode(MessageDeliveryMode.PERSISTENT);
            message.getMessageProperties().setHeader("task", taskName);
            message.getMessageProperties().setHeader("id", taskId);
            message.getMessageProperties().setHeader("lang", "py");
            message.getMessageProperties().setHeader("retries", 0);
            rabbitTemplate.convertAndSend(EXCHANGE, routingKey, message, (MessagePostProcessor) m -> m);
            log.info("任务已投递 task={} id={} routing={}", taskName, taskId, routingKey);
        } catch (Exception e) {
            // 投递失败如实上抛：会话保持 running 由对账/人工介入，不静默吞掉（A-16）
            log.error("MQ 投递失败 task={} id={}", taskName, taskId, e);
            throw new IllegalStateException("MQ 投递失败: " + taskName, e);
        }
    }

    @Configuration
    static class MqConfig {
        @Bean
        TopicExchange sonarcadTaskExchange() {
            return new TopicExchange(EXCHANGE, true, false);
        }
    }
}
