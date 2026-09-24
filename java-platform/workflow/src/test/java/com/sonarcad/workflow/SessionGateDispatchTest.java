package com.sonarcad.workflow;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sonarcad.workflow.mq.TaskPublisher;
import com.sonarcad.workflow.notify.NotifyClient;
import com.sonarcad.workflow.session.DesignSessionEntity;
import com.sonarcad.workflow.session.DesignSessionMapper;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.session.ProjectLookupMapper;
import com.sonarcad.workflow.session.SessionService;
import com.sonarcad.workflow.session.UserLookupMapper;
import com.sonarcad.workflow.statemachine.PipelineEvent;
import com.sonarcad.workflow.statemachine.PipelineStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.web.server.ResponseStatusException;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/**
 * T2.2 审阅关接线单测：STAGE_DONE 分支按 current_stage 续派或暂停（mock 持久层，不起 Spring 容器）。
 */
class SessionGateDispatchTest {

    private DesignSessionMapper sessionMapper;
    private IrSnapshotMapper irSnapshotMapper;
    private TaskPublisher taskPublisher;
    private ProjectLookupMapper projectLookupMapper;
    private com.sonarcad.workflow.session.KbBaseLookupMapper kbBaseLookupMapper;
    private SessionService service;

    @BeforeEach
    void setUp() {
        sessionMapper = mock(DesignSessionMapper.class);
        irSnapshotMapper = mock(IrSnapshotMapper.class);
        taskPublisher = mock(TaskPublisher.class);
        projectLookupMapper = mock(ProjectLookupMapper.class);
        kbBaseLookupMapper = mock(com.sonarcad.workflow.session.KbBaseLookupMapper.class);
        // 真实 NotifyClient：发布为异步 fire-and-forget，测试中连不上只记日志、不影响断言
        service = new SessionService(sessionMapper, irSnapshotMapper, taskPublisher,
                new NotifyClient("http://127.0.0.1:1"), mock(UserLookupMapper.class),
                projectLookupMapper, kbBaseLookupMapper,
                mock(com.sonarcad.workflow.session.CadTuneLookupMapper.class));
    }

    @Test
    void listFillsProjectNames() {
        // 纯 Mockito 不跑 MyBatis 启动，lambda 列缓存需手动注册（fillProjectNames 用 LambdaQueryWrapper）
        com.baomidou.mybatisplus.core.metadata.TableInfoHelper.initTableInfo(
                new org.apache.ibatis.builder.MapperBuilderAssistant(
                        new com.baomidou.mybatisplus.core.MybatisConfiguration(), ""),
                com.sonarcad.workflow.session.ProjectLookupEntity.class);
        DesignSessionEntity s = session("s1", "pending", null);
        s.setProjectId("p-1");
        Page<DesignSessionEntity> page = new Page<>(1, 10);
        page.setRecords(java.util.List.of(s));
        when(sessionMapper.selectPage(any(), any())).thenReturn(page);
        com.sonarcad.workflow.session.ProjectLookupEntity pe =
                new com.sonarcad.workflow.session.ProjectLookupEntity();
        pe.setId("p-1");
        pe.setName("人工测试");
        when(projectLookupMapper.selectList(any())).thenReturn(java.util.List.of(pe));

        Page<DesignSessionEntity> out = service.list(null, 1, 10);
        assertEquals("人工测试", out.getRecords().get(0).getProjectName());
    }

    @Test
    void listFillsKbNames() {
        com.baomidou.mybatisplus.core.metadata.TableInfoHelper.initTableInfo(
                new org.apache.ibatis.builder.MapperBuilderAssistant(
                        new com.baomidou.mybatisplus.core.MybatisConfiguration(), ""),
                com.sonarcad.workflow.session.KbBaseLookupEntity.class);
        DesignSessionEntity s = session("s1", "pending", null);
        s.setKbIds("[\"kb-1\",\"kb-2\"]");
        Page<DesignSessionEntity> page = new Page<>(1, 10);
        page.setRecords(java.util.List.of(s));
        when(sessionMapper.selectPage(any(), any())).thenReturn(page);
        when(projectLookupMapper.selectList(any())).thenReturn(java.util.List.of());
        com.sonarcad.workflow.session.KbBaseLookupEntity kb1 =
                new com.sonarcad.workflow.session.KbBaseLookupEntity();
        kb1.setId("kb-1");
        kb1.setName("默认知识库");
        when(kbBaseLookupMapper.selectList(any())).thenReturn(java.util.List.of(kb1));

        Page<DesignSessionEntity> out = service.list(null, 1, 10);
        // kb-2 已被删除（悬空引用）：只回填存在的库名，不炸、不补假名（A-16）
        assertEquals(java.util.List.of("默认知识库"), out.getRecords().get(0).getKbNames());
    }

    @Test
    void createPersistsKbIds() {
        com.baomidou.mybatisplus.core.metadata.TableInfoHelper.initTableInfo(
                new org.apache.ibatis.builder.MapperBuilderAssistant(
                        new com.baomidou.mybatisplus.core.MybatisConfiguration(), ""),
                com.sonarcad.workflow.session.KbBaseLookupEntity.class);
        com.sonarcad.workflow.session.KbBaseLookupEntity kb =
                new com.sonarcad.workflow.session.KbBaseLookupEntity();
        // uuid 列（V9）→ 非法格式在预检即 400；用合法 uuid 才走到 mapper stub
        kb.setId("0eb22589-055c-4453-921a-42206e2e6948");
        kb.setName("默认知识库");
        when(kbBaseLookupMapper.selectList(any())).thenReturn(java.util.List.of(kb));

        service.create("p-1", "标题", "需求",
                java.util.List.of("0eb22589-055c-4453-921a-42206e2e6948"), "u1");

        ArgumentCaptor<DesignSessionEntity> inserted = ArgumentCaptor.forClass(DesignSessionEntity.class);
        verify(sessionMapper).insert(inserted.capture());
        assertEquals(java.util.List.of("0eb22589-055c-4453-921a-42206e2e6948"),
                SessionService.parseKbIds(inserted.getValue().getKbIds()));
    }

    @Test
    void createRejectsMalformedKbId() {
        // 非法 uuid 格式：预检直接 400（不触 DB——真实场景曾打出 500）
        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> service.create("p-1", "标题", "需求", java.util.List.of("kb-1"), "u1"));
        assertEquals(400, ex.getStatusCode().value());
    }

    @Test
    void createRejectsUnknownKbId() {
        when(kbBaseLookupMapper.selectList(any())).thenReturn(java.util.List.of());

        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> service.create("p-1", "标题", "需求",
                        java.util.List.of("00000000-0000-0000-0000-000000000000"), "u1"));
        assertEquals(400, ex.getStatusCode().value());
    }

    @Test
    void listFillsActiveTuneStatus() {
        // PHASE-008 T8.6：活跃微调会话回填（审阅警示与会话列表口径）
        com.baomidou.mybatisplus.core.metadata.TableInfoHelper.initTableInfo(
                new org.apache.ibatis.builder.MapperBuilderAssistant(
                        new com.baomidou.mybatisplus.core.MybatisConfiguration(), ""),
                com.sonarcad.workflow.session.CadTuneLookupEntity.class);
        com.sonarcad.workflow.session.CadTuneLookupMapper cadTuneLookupMapper =
                mock(com.sonarcad.workflow.session.CadTuneLookupMapper.class);
        service = new SessionService(sessionMapper, irSnapshotMapper, taskPublisher,
                new NotifyClient("http://127.0.0.1:1"), mock(UserLookupMapper.class),
                projectLookupMapper, kbBaseLookupMapper, cadTuneLookupMapper);
        DesignSessionEntity s = session("s1nodash", "paused", "gate2");
        Page<DesignSessionEntity> page = new Page<>(1, 10);
        page.setRecords(java.util.List.of(s));
        when(sessionMapper.selectPage(any(), any())).thenReturn(page);
        when(projectLookupMapper.selectList(any())).thenReturn(java.util.List.of());
        com.sonarcad.workflow.session.CadTuneLookupEntity t =
                new com.sonarcad.workflow.session.CadTuneLookupEntity();
        t.setId("tune-1");
        // 列读回带横线（uuid 原生形态）——回填需与归一化无横线 id 对齐
        t.setDesignSessionId("s1-no-dash");
        t.setStatus("active");
        when(cadTuneLookupMapper.selectList(any())).thenReturn(java.util.List.of(t));

        Page<DesignSessionEntity> out = service.list(null, 1, 10);
        assertEquals("tune-1", out.getRecords().get(0).getTuneId());
        assertEquals("active", out.getRecords().get(0).getTuneStatus());
    }

    @Test
    void listWithoutActiveTuneLeavesFieldsNull() {
        com.baomidou.mybatisplus.core.metadata.TableInfoHelper.initTableInfo(
                new org.apache.ibatis.builder.MapperBuilderAssistant(
                        new com.baomidou.mybatisplus.core.MybatisConfiguration(), ""),
                com.sonarcad.workflow.session.CadTuneLookupEntity.class);
        com.sonarcad.workflow.session.CadTuneLookupMapper cadTuneLookupMapper =
                mock(com.sonarcad.workflow.session.CadTuneLookupMapper.class);
        service = new SessionService(sessionMapper, irSnapshotMapper, taskPublisher,
                new NotifyClient("http://127.0.0.1:1"), mock(UserLookupMapper.class),
                projectLookupMapper, kbBaseLookupMapper, cadTuneLookupMapper);
        DesignSessionEntity s = session("s1", "paused", "gate2");
        Page<DesignSessionEntity> page = new Page<>(1, 10);
        page.setRecords(java.util.List.of(s));
        when(sessionMapper.selectPage(any(), any())).thenReturn(page);
        when(projectLookupMapper.selectList(any())).thenReturn(java.util.List.of());
        when(cadTuneLookupMapper.selectList(any())).thenReturn(java.util.List.of());

        Page<DesignSessionEntity> out = service.list(null, 1, 10);
        assertNull(out.getRecords().get(0).getTuneId());
        assertNull(out.getRecords().get(0).getTuneStatus());
    }

    private DesignSessionEntity session(String id, String status, String stage) {
        DesignSessionEntity s = new DesignSessionEntity();
        s.setId(id);
        s.setPipelineStatus(status);
        s.setCurrentStage(stage);
        return s;
    }

    @Test
    void stageDoneAfterA2PausesGate1() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A2"));
        when(sessionMapper.update(any(), any())).thenReturn(1);
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A2"));

        service.fireEvent("s1", PipelineEvent.STAGE_DONE);

        ArgumentCaptor<DesignSessionEntity> patch = ArgumentCaptor.forClass(DesignSessionEntity.class);
        verify(sessionMapper).update(patch.capture(), any());
        assertEquals(PipelineStatus.paused.name(), patch.getValue().getPipelineStatus());
        assertEquals("gate1", patch.getValue().getCurrentStage());
        verify(taskPublisher, never()).publishA3Task(any());
    }

    @Test
    void stageDoneAfterA4PausesGate2() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A4"));
        when(sessionMapper.update(any(), any())).thenReturn(1);

        service.fireEvent("s1", PipelineEvent.STAGE_DONE);

        ArgumentCaptor<DesignSessionEntity> patch = ArgumentCaptor.forClass(DesignSessionEntity.class);
        verify(sessionMapper).update(patch.capture(), any());
        assertEquals(PipelineStatus.paused.name(), patch.getValue().getPipelineStatus());
        assertEquals("gate2", patch.getValue().getCurrentStage());
    }

    @Test
    void stageDoneAfterA3DispatchesA4() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A3"));

        service.fireEvent("s1", PipelineEvent.STAGE_DONE);

        verify(taskPublisher).publishA4Task("s1");
        verify(sessionMapper, never()).update(any(), any());
    }

    @Test
    void stageDoneAfterA1DispatchesA2() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "running", "A1"));

        service.fireEvent("s1", PipelineEvent.STAGE_DONE);

        verify(taskPublisher).publishA2Task("s1");
    }

    @Test
    void pauseConflictSurfaces409() {
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "completed", "A2"));
        when(sessionMapper.update(any(), any())).thenReturn(0);

        assertThrows(ResponseStatusException.class,
                () -> service.fireEvent("s1", PipelineEvent.STAGE_DONE));
        verify(taskPublisher, never()).publishA3Task(any());
    }

    @Test
    void startMarksStageA1Immediately() {
        // 启动空窗修复（2026-09-19 用户反馈）：START 即落 current_stage=A1，不等 worker 回写
        when(sessionMapper.selectById("s1")).thenReturn(session("s1", "pending", null));
        when(sessionMapper.update(any(), any())).thenReturn(1);

        DesignSessionEntity out = service.fireEvent("s1", PipelineEvent.START);

        assertEquals(PipelineStatus.running.name(), out.getPipelineStatus());
        assertEquals("A1", out.getCurrentStage());
        ArgumentCaptor<DesignSessionEntity> patch = ArgumentCaptor.forClass(DesignSessionEntity.class);
        verify(sessionMapper).update(patch.capture(), any());
        assertEquals("A1", patch.getValue().getCurrentStage());
        verify(taskPublisher).publishA1Task("s1");
    }
}
