package com.sonarcad.workflow;

import com.sonarcad.workflow.session.IrSnapshotEntity;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.web.IrSnapshotController;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** T15.6/C-2 读侧配套：IR 快照按版本读取端点（批准组合卡全参数 diff 数据源）。 */
class IrSnapshotVersionTest {

    @Test
    void versionEndpointReturnsSnapshotEnvelope() {
        IrSnapshotMapper mapper = mock(IrSnapshotMapper.class);
        IrSnapshotController controller = new IrSnapshotController(mapper);
        IrSnapshotEntity e = new IrSnapshotEntity();
        e.setId("IR2-3");
        e.setSessionId("s1");
        e.setIrType("IR2");
        e.setVersion(3);
        e.setSchemaVersion("v1");
        e.setCreatedBy("A2");
        e.setInvalidatedAt(OffsetDateTime.now());
        e.setContent("{\"parameters\": []}");
        when(mapper.selectOne(any())).thenReturn(e);

        Map<String, Object> data = controller.version("s1", "ir2", 3).data();
        assertEquals("IR2", data.get("ir_type"));
        assertEquals(3, data.get("version"));
        assertTrue((Boolean) data.get("invalidated"), "失效版本仍可读，由 invalidated 标注");
        assertEquals("{\"parameters\": []}", data.get("content"));
    }

    @Test
    void versionEndpointThrowsWhenMissing() {
        IrSnapshotMapper mapper = mock(IrSnapshotMapper.class);
        IrSnapshotController controller = new IrSnapshotController(mapper);
        when(mapper.selectOne(any())).thenReturn(null);

        assertThrows(IllegalArgumentException.class, () -> controller.version("s1", "IR2", 9));
    }

    @Test
    void versionEndpointRejectsIllegalType() {
        IrSnapshotController controller = new IrSnapshotController(mock(IrSnapshotMapper.class));
        assertThrows(IllegalArgumentException.class, () -> controller.version("s1", "IR9", 1));
    }
}
