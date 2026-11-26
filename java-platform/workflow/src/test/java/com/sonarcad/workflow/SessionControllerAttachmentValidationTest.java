package com.sonarcad.workflow;

import com.sonarcad.workflow.audit.AuditRecorder;
import com.sonarcad.workflow.session.AttachmentRef;
import com.sonarcad.workflow.session.DesignSessionMapper;
import com.sonarcad.workflow.session.IrSnapshotMapper;
import com.sonarcad.workflow.session.ProjectLookupMapper;
import com.sonarcad.workflow.session.ProjectVisibilityMapper;
import com.sonarcad.workflow.session.SessionCleanupService;
import com.sonarcad.workflow.session.SessionService;
import com.sonarcad.workflow.session.UserLookupMapper;
import com.sonarcad.workflow.web.SessionController;
import com.sonarcad.workflow.web.SessionController.CreateSessionRequest;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/** T18.5（ADR-017）：创建请求附件引用白名单校验（controller 命令式校验层）。 */
class SessionControllerAttachmentValidationTest {

    private SessionController controllerWithMockDeps() {
        SessionService service = mock(SessionService.class);
        when(service.create(any(), any(), any(), any(), any(), any()))
                .thenReturn(new com.sonarcad.workflow.session.DesignSessionEntity());
        return new SessionController(service, mock(SessionCleanupService.class),
                mock(AuditRecorder.class), mock(ProjectVisibilityMapper.class));
    }

    @Test
    void rejectsForeignBucket() {
        SessionController c = controllerWithMockDeps();
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
                () -> c.create(new CreateSessionRequest("p-1", "标题", "需求", null,
                        java.util.List.of(new AttachmentRef("step-models", "0123456789abcdef/a.step", null))),
                        "u1", "admin", null));
        assertTrue(ex.getMessage().contains("session-attachments"));
    }

    @Test
    void rejectsMalformedObjectKey() {
        SessionController c = controllerWithMockDeps();
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
                () -> c.create(new CreateSessionRequest("p-1", "标题", "需求", null,
                        java.util.List.of(new AttachmentRef("session-attachments", "../etc/passwd", null))),
                        "u1", "admin", null));
        assertTrue(ex.getMessage().contains("object_key"));
    }

    @Test
    void rejectsFourthAttachment() {
        SessionController c = controllerWithMockDeps();
        AttachmentRef ok = new AttachmentRef("session-attachments", "0123456789abcdef/a.png", null);
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
                () -> c.create(new CreateSessionRequest("p-1", "标题", "需求", null,
                        java.util.List.of(ok, ok, ok, ok)), "u1", "admin", null));
        assertTrue(ex.getMessage().contains("最多 3 张"));
    }

    @Test
    void validAttachmentsPassThrough() {
        SessionController c = controllerWithMockDeps();
        jakarta.servlet.http.HttpServletRequest req = mock(jakarta.servlet.http.HttpServletRequest.class);
        var resp = c.create(new CreateSessionRequest("p-1", "标题", "需求", null,
                        java.util.List.of(new AttachmentRef("session-attachments", "0123456789abcdef/a.png", null))),
                "u1", "admin", req);
        assertNotNull(resp);
    }
}
