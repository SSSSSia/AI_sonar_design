package com.sonarcad.gateway.config;

import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.timelimiter.TimeLimiterConfig;
import org.springframework.cloud.circuitbreaker.resilience4j.Resilience4JCircuitBreakerFactory;
import org.springframework.cloud.circuitbreaker.resilience4j.Resilience4JConfigBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

/**
 * T13.5 实测返修：Spring Cloud Gateway 的 CircuitBreaker 过滤器默认 TimeLimiter=1s——
 * py-engine 慢端点（PDF 转换 gotenberg ~8s 实测）被掐断进 fallback（误报「熔断保护中」）。
 * yaml 方式（resilience4j.timelimiter.instances）在该 starter 装配下未生效（实测 1.0s 仍 503），
 * 改为编程式 Customizer：pyEngine 路由超时 120s（对齐 gotenberg 转换预算 + DOCGEN 超时链）。
 * 熔断阈值语义维持 T9.4 口径（yaml circuitbreaker.instances 与此处 build 一致，显式声明为准）。
 */
@Configuration
public class ResilienceCustomizerConfig {

    @Bean
    public org.springframework.cloud.client.circuitbreaker.Customizer<Resilience4JCircuitBreakerFactory>
    gatewayResilienceCustomizer() {
        return factory -> {
            TimeLimiterConfig timeLimiter = TimeLimiterConfig.custom()
                    .timeoutDuration(Duration.ofSeconds(120))
                    .cancelRunningFuture(false)
                    .build();
            CircuitBreakerConfig breaker = CircuitBreakerConfig.custom()
                    .slidingWindowSize(20)
                    .minimumNumberOfCalls(10)
                    .failureRateThreshold(60)
                    .waitDurationInOpenState(Duration.ofSeconds(30))
                    .permittedNumberOfCallsInHalfOpenState(3)
                    .build();
            factory.configure(builder -> builder
                    .timeLimiterConfig(timeLimiter)
                    .circuitBreakerConfig(breaker), "pyEngine");
            factory.configureDefault(id -> new Resilience4JConfigBuilder(id)
                    .timeLimiterConfig(timeLimiter)
                    .circuitBreakerConfig(breaker)
                    .build());
        };
    }
}
