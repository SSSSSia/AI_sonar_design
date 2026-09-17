package com.sonarcad.workflow;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sonarcad.workflow.session.IrSnapshotEntity;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.session.TraceChainEntity;
import com.sonarcad.workflow.session.TraceChainMapper;
import com.sonarcad.workflow.web.TraceController;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** T2.4 溯源反查单测：链路步骤 + IR0/IR1/IR2 上下文组装。 */
class TraceControllerTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    private TraceChainMapper traceChainMapper;
    private IrSnapshotMapper irSnapshotMapper;
    private TraceController controller;

    @BeforeEach
    void setUp() {
        traceChainMapper = mock(TraceChainMapper.class);
        irSnapshotMapper = mock(IrSnapshotMapper.class);
        controller = new TraceController(traceChainMapper, irSnapshotMapper);
    }

    private TraceChainEntity step(int no, String actor, String action, String payload) {
        TraceChainEntity e = new TraceChainEntity();
        e.setTraceId("tr-p-001");
        e.setStepNo(no);
        e.setSessionId("s1");
        e.setActor(actor);
        e.setAction(action);
        e.setIrSnapshotId("IR2-1");
        e.setPayload(payload);
        return e;
    }

    private IrSnapshotEntity snapshot(String irType, int version, Object content) throws Exception {
        IrSnapshotEntity e = new IrSnapshotEntity();
        e.setId(irType + "-" + version);
        e.setSessionId("s1");
        e.setIrType(irType);
        e.setVersion(version);
        e.setContent(JSON.writeValueAsString(content));
        return e;
    }

    @Test
    void traceAssemblesStepsAndContextBackToIr0() throws Exception {
        when(traceChainMapper.selectList(any(LambdaQueryWrapper.class))).thenReturn(List.of(
                step(1, "A2", "derive", "{\"param_id\": \"P-001\"}"),
                step(2, "A4", "validate", "{\"constraint_id\": \"C-1\"}")
        ));
        when(irSnapshotMapper.selectOne(any(LambdaQueryWrapper.class))).thenReturn(
                snapshot("IR0", 1, Map.of("raw_text", "设计 16 阵元拖曳线列阵")),
                snapshot("IR1", 1, Map.of(
                        "objectives", List.of(Map.of("dimension", "声阵规模", "value", 16)),
                        "retrieved_context", List.of(Map.of("doc_id", "kb.txt", "location", "段落1")))),
                snapshot("IR2", 1, Map.of("parameters", List.of(
                        Map.of("id", "P-001", "name", "阵元总数", "trace_id", "tr-p-001")))));

        Map<String, Object> data = controller.trace("tr-p-001").data();

        assertEquals("tr-p-001", data.get("trace_id"));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> steps = (List<Map<String, Object>>) data.get("steps");
        assertEquals(2, steps.size());
        assertEquals("derive", steps.get(0).get("action"));

        @SuppressWarnings("unchecked")
        Map<String, Object> context = (Map<String, Object>) data.get("context");
        assertEquals("设计 16 阵元拖曳线列阵", context.get("ir0_raw_text"));
        assertNotNull(context.get("ir1_retrieved_refs"));
        @SuppressWarnings("unchecked")
        Map<String, Object> param = (Map<String, Object>) context.get("ir2_param");
        assertEquals("P-001", param.get("id"));
    }

    @Test
    void unknownTraceIdRejected() {
        when(traceChainMapper.selectList(any(LambdaQueryWrapper.class))).thenReturn(List.of());
        assertThrows(IllegalArgumentException.class, () -> controller.trace("tr-nope"));
    }
}
