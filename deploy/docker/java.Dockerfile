# T9.5（PHASE-009）Java 五服务共用镜像：构建期打全部 jar，运行期以 SERVICE_JAR 选服务
# （compose：gateway/auth/workflow/biz/notify 各起一个容器，environment.SERVICE_JAR 区分）
# 构建：docker build -f deploy/docker/java.Dockerfile .（上下文 = 仓库根）
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /src

# 依赖层缓存（先拷 pom 再 go-offline，业务代码变动不重下依赖）
COPY java-platform/pom.xml ./pom.xml
COPY java-platform/gateway/pom.xml ./gateway/pom.xml
COPY java-platform/auth/pom.xml ./auth/pom.xml
COPY java-platform/workflow/pom.xml ./workflow/pom.xml
COPY java-platform/biz/pom.xml ./biz/pom.xml
COPY java-platform/notify/pom.xml ./notify/pom.xml
RUN mvn -B -q dependency:go-offline || true

COPY java-platform/ ./
RUN mvn -B -q -DskipTests package

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /src/gateway/target/gateway-*-SNAPSHOT.jar ./gateway.jar
COPY --from=build /src/auth/target/auth-*-SNAPSHOT.jar ./auth.jar
COPY --from=build /src/workflow/target/workflow-*-SNAPSHOT.jar ./workflow.jar
COPY --from=build /src/biz/target/biz-*-SNAPSHOT.jar ./biz.jar
COPY --from=build /src/notify/target/notify-*-SNAPSHOT.jar ./notify.jar

# SERVICE_JAR ∈ gateway|auth|workflow|biz|notify（compose environment 注入）
ENTRYPOINT ["sh", "-c", "exec java -jar /app/${SERVICE_JAR:-gateway}.jar"]
