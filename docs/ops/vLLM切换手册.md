# vLLM 私有化切换操作手册（T11.6）

> 适用：声呐平台 v0.4+。平台 LLM 调用**只走 OpenAI 兼容协议**（`py-engine/llm/adapter.py`），
> vLLM 服务本身就是 OpenAI 兼容端点——切换 = 注册一条 provider 行，无需改代码。
> 真实部署依赖昇腾 910C / GPU 硬件到位（GAP-001 外部依赖）；本手册由 T11.6 以 OpenAI 兼容
> mock 验证过全链路（注册 → 热切换 → 出站脱敏 → 失败转移）。

## 1. 服务端：启动 vLLM（OpenAI 兼容）

### 1.1 通用 GPU（CUDA）

```bash
pip install vllm
python -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 --port 8000 \
  --model /models/Qwen2.5-14B-Instruct \
  --served-model-name sonarcad-llm \
  --max-model-len 32768
```

### 1.2 昇腾 910C（vLLM-Ascend 插件）

```bash
# 依 vllm-ascend 官方指引安装（含 CANN 驱动）；推理入口同 OpenAI 兼容
python -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 --port 8000 \
  --model /models/Qwen2.5-14B-Instruct --served-model-name sonarcad-llm
```

自检（任一形态）：

```bash
curl http://<vllm-host>:8000/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"sonarcad-llm","messages":[{"role":"user","content":"ping"}]}'
```

## 2. 平台侧：注册 provider（热切换，≤30s 生效）

管理界面：知识库管理 → LLM 供应商页签（admin）。或 API：

```bash
curl -X POST http://<gateway>/api/v1/kb/llm-providers \
  -H "Authorization: Bearer <admin-jwt>" -H "Content-Type: application/json" \
  -d '{"name":"vllm-ascend-1","base_url":"http://<vllm-host>:8000/v1",
       "api_key":"<vllm-token或占位>","model":"sonarcad-llm",
       "priority":1000,"enabled":true,"timeout_s":300}'
```

要点：
- **priority 越大越优先**：置 1000 即立即成为主供应商；原云端供应商保持 enabled 且降
  priority 即自动成为**失败转移备选**（见 §3）。
- 生效无需重启：py-engine 按 TTL（30s）热加载 provider 表，或立即
  `POST /api/v1/llm/provider/reload`（同时清 L1 LLM 缓存）。
- 健康可见：`GET /api/v1/llm/provider` 返回 `health[]`（各供应商最近一次调用成败）。
  （health 为**进程内**记录：uvicorn API 与 celery worker 各自独立——与 T10.1 缓存计数同口径；worker 侧调用成败看 Prometheus `py_llm_call_*` 指标）
- api_key 只写不回显（列表/详情响应恒脱敏）。

## 3. 失败转移（T11.6，默认开启）

主供应商重试（`LLM_MAX_RETRIES`，默认 2）耗尽后，自动切到 priority 次高的 enabled
供应商再试；全链失败才抛错。`LLM_FAILOVER=0` 可关闭（仅用主供应商）。

推荐生产配置：`vllm-ascend-1`（priority 1000，主）+ 现有云端供应商（priority 100，备），
主备语义天然形成。

## 4. 出站内容脱敏（T11.6，默认开启）

- 所有经平台的 LLM 出站 prompt 做**键值脱敏**（sk- key / Bearer token / password 赋值 /
  带凭据连接串 / .env 形态密钥行），命中替换为 `[REDACTED:类别]` 并记审计日志
  （只记类别与次数，不记原值）。
- 关闭（不建议）：py-engine 环境 `SANITIZE_LLM_CONTENT=0`。
- L1 缓存键基于脱敏后内容——脱敏只影响命中面，不影响缓存正确性。

## 5. 回退

1. 供应商页签将 vLLM 行 `enabled=false`（或 priority 降为备选）；
2. `POST /api/v1/llm/provider/reload`；
3. `GET /api/v1/llm/provider` 确认 active 回到原供应商。

## 6. 验收清单（切换完成后逐项勾）

- [ ] vLLM 服务自检 curl 返回补全
- [ ] 平台注册后 `GET /api/v1/kb/llm-providers` 可见且 api_key 不回显
- [ ] 发起一次真实设计会话，`GET /api/v1/llm/provider` health[vllm]=ok
- [ ] （可选）停 vLLM 服务发起会话——失败转移日志出现 provider 切换、会话仍正常完成
