"""Celery 应用（T1.2，D-06/D-05）。

- broker=RabbitMQ；backend=Redis（连接见 app_config）。
- 任务队列 sonarcad.echo 绑定 topic 交换机 sonarcad.task（routing_key=task.echo），
  由 Java workflow 在会话 START 事件时投递（对端：workflow/TaskPublisher.java）。
- 失败任务投递到死信交换机 sonarcad.dlx（queue sonarcad.dlq）隔离，可人工排查重放。
- worker 无状态：任务结果只经回调/共享存储落地，worker 重启不丢已发布消息。
"""

from __future__ import annotations

from kombu import Exchange, Queue

from celery import Celery

from app_config import (
    CELERY_BROKER_URL,
    CELERY_RESULT_BACKEND,
    TASK_HARD_TIME_LIMIT_S,
    TASK_MAX_RETRIES,
    TASK_SOFT_TIME_LIMIT_S,
)

task_exchange = Exchange("sonarcad.task", type="topic", durable=True)
dead_letter_exchange = Exchange("sonarcad.dlx", type="topic", durable=True)

celery_app = Celery(
    "sonarcad",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_queues=(
        Queue(
            "sonarcad.echo",
            exchange=task_exchange,
            routing_key="task.echo",
            durable=True,
            queue_arguments={
                # 消费失败/拒绝且不重投的消息进入死信交换机（A-13：隔离，不静默）
                "x-dead-letter-exchange": "sonarcad.dlx",
                "x-dead-letter-routing-key": "dead",
            },
        ),
        Queue(
            "sonarcad.a1",
            exchange=task_exchange,
            routing_key="task.a1",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": "sonarcad.dlx",
                "x-dead-letter-routing-key": "dead",
            },
        ),
        Queue(
            "sonarcad.a2",
            exchange=task_exchange,
            routing_key="task.a2",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": "sonarcad.dlx",
                "x-dead-letter-routing-key": "dead",
            },
        ),
        Queue(
            "sonarcad.a3",
            exchange=task_exchange,
            routing_key="task.a3",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": "sonarcad.dlx",
                "x-dead-letter-routing-key": "dead",
            },
        ),
        Queue(
            "sonarcad.a4",
            exchange=task_exchange,
            routing_key="task.a4",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": "sonarcad.dlx",
                "x-dead-letter-routing-key": "dead",
            },
        ),
        Queue(
            "sonarcad.a5",
            exchange=task_exchange,
            routing_key="task.a5",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": "sonarcad.dlx",
                "x-dead-letter-routing-key": "dead",
            },
        ),
        Queue(
            "sonarcad.kb",
            exchange=task_exchange,
            routing_key="task.kb",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": "sonarcad.dlx",
                "x-dead-letter-routing-key": "dead",
            },
        ),
        # 注意：sonarcad.dlq 不放 task_queues——放这里会被 worker 默认订阅，
        # 死信被再次消费形成回环。DLQ 只允许人工排查/重放。
    ),
    task_default_exchange="sonarcad.task",
    task_default_exchange_type="topic",
    task_routes=[
        {"sonarcad.a1": {"queue": "sonarcad.a1"}},
        {"sonarcad.a2": {"queue": "sonarcad.a2"}},
        {"sonarcad.a3": {"queue": "sonarcad.a3"}},
        {"sonarcad.a4": {"queue": "sonarcad.a4"}},
        {"sonarcad.a5": {"queue": "sonarcad.a5"}},
        {"sonarcad.kb_build": {"queue": "sonarcad.kb"}},
        {"sonarcad.*": {"queue": "sonarcad.echo"}},
    ],
    task_acks_late=True,                      # 执行成功才 ack，崩溃不丢任务
    worker_prefetch_multiplier=1,             # 长任务公平分发
    # T10.3 基线实测返修：--pool=solo 下任务跑主循环，>~2min 的任务（A2 真实 LLM 286s /
    # A3 177s）阻塞 AMQP 心跳 → broker 判死连接 → ack 随连接丢失 → 任务 100% 重投
    # （短任务不复现；重投又级联双派下游）。broker_heartbeat=0 关闭心跳协商，
    # 换取长任务不被误杀（连接真死由 publish 重试与消费重连兜底）。
    broker_heartbeat=0,
    result_expires=3600,
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT_S,
    task_time_limit=TASK_HARD_TIME_LIMIT_S,
    broker_connection_retry_on_startup=True,
    # 有界终止（A-13）：默认重试上限；个别任务可自行覆盖
    task_publish_retry=True,
    task_publish_retry_max_retries=TASK_MAX_RETRIES,
)


from celery.signals import worker_ready  # noqa: E402


@worker_ready.connect
def declare_dead_letter_queue(**_kwargs):
    """worker 启动时声明死信队列（幂等）。放在 task_queues 外，确保不被自动消费。"""
    with celery_app.connection() as conn:
        dlq = Queue("sonarcad.dlq", exchange=dead_letter_exchange, routing_key="dead", durable=True)
        dlq(conn.default_channel).declare()


@worker_ready.connect
def start_metrics_endpoint(**_kwargs):
    """worker 进程独立 /metrics（T10.1a）：API 进程无法看到的任务/沙箱/检索指标由此暴露。

    --pool=solo（现部署形态）下任务在主进程执行，进程内计数可见；若未来改 prefork 池，
    子进程计数不会回流 master，需迁 prometheus_client multiprocess 模式——如实记录，勿静默换池。
    """
    import logging

    from app_config import METRICS_PORT
    from observability.metrics import start_metrics_server

    start_metrics_server(METRICS_PORT)

# 关键：让本 app 成为 current/default，否则 api 进程里 shared_task.delay()
# 会绑到 Celery 内置默认 app（消息进默认 celery 队列、路由/死信配置全部失效）
celery_app.set_current()
celery_app.set_default()
