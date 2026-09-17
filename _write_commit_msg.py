msg = """feat(scaffold): 阶段0 T0.1 仓库骨架——三端脚手架与目录结构

- java-platform：Maven 多模块（gateway/auth/workflow/biz/notify），Spring Boot 3.3.5 + Spring Cloud 2023.0.3 + StateMachine 4.0.0，mvn compile 通过
- py-engine：FastAPI 骨架 + A1-A5 目录（docstring 引用宪法 §3.1 边界表）+ 冒烟测试通过
- frontend：Vite 6 + React 19 + TS + AntD 5，vite build 通过
- contracts/ deploy/ 占位；.env.example；.gitignore 补 target/
- roadmap T0.1 回填完成，devlog 记录
"""
with open(r"D:/CODE_FILE/CODE_PYTHON/AI_sonar_design/_commit_msg.txt", "w", encoding="utf-8") as f:
    f.write(msg)
print("written")
