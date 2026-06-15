import pytest

from agentkit.kernel.permission import (
    Autonomy,
    Permission,
    PermissionDenied,
    PermissionLayer,
    PermissionResolver,
    PermissionRule,
)


def test_deny_hard_wall_even_under_auto():
    resolver = PermissionResolver.default()

    with pytest.raises(PermissionDenied):
        resolver.assert_allowed(
            tool_name="bash",
            bucket="bash",
            action="rm -rf /tmp/x",
            autonomy=Autonomy.AUTO,
        )


def test_ask_headless_downgrades_to_deny():
    resolver = PermissionResolver.default()

    with pytest.raises(PermissionDenied):
        resolver.assert_allowed(
            tool_name="bash",
            bucket="bash",
            action="echo ok",
            autonomy=Autonomy.ASSIST,
            interactive=False,
        )


def test_unknown_permission_bucket_defaults_to_deny():
    with pytest.raises(PermissionDenied):
        PermissionResolver.default().assert_allowed(
            tool_name="external",
            bucket="mystery",
            autonomy=Autonomy.AUTO,
        )


def test_mcp_defaults_to_ask_and_headless_denies():
    with pytest.raises(PermissionDenied):
        PermissionResolver.default().assert_allowed(
            tool_name="server.tool",
            bucket="mcp",
            autonomy=Autonomy.ASSIST,
            interactive=False,
        )


def test_bash_dangerous_command_is_detected_after_shell_wrapper():
    with pytest.raises(PermissionDenied):
        PermissionResolver.default().assert_allowed(
            tool_name="bash",
            bucket="bash",
            action="echo ok; rm -rf /tmp/x",
            autonomy=Autonomy.AUTO,
        )


def test_strictest_layer_cannot_widen_deny():
    resolver = PermissionResolver(
        [
            PermissionLayer(
                name="base",
                rules={"write": [PermissionRule(pattern="*", permission=Permission.DENY)]},
            ),
            PermissionLayer(
                name="skill",
                rules={"write": [PermissionRule(pattern="*", permission=Permission.ALLOW)]},
            ),
        ]
    )

    assert resolver.resolve(bucket="write").permission is Permission.DENY
