import subprocess

CHECKS = [
    ("java", "java -version"),
    ("mvn", "mvn -version"),
    ("docker", "docker --version"),
    ("node", "node -v"),
    ("git_status", "git -C D:/CODE_FILE/CODE_PYTHON/AI_sonar_design status --short"),
    ("git_log", "git -C D:/CODE_FILE/CODE_PYTHON/AI_sonar_design log --oneline -3"),
]

lines = []
for name, cmd in CHECKS:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=60)
        out = (p.stdout or p.stderr or "").strip()
        lines.append(f"=== {name} (exit {p.returncode}) ===\n{out[:800]}")
    except Exception as e:
        lines.append(f"=== {name} === ERROR: {e}")

with open(r"D:/CODE_FILE/CODE_PYTHON/AI_sonar_design/_envcheck.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("done")
