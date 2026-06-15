from agentkit import Agent
from agentkit.subsystems.fusion import FusionProvider


def test_agent_config_builds_fusion_provider_from_nested_specs():
    agent = Agent(
        config={
            "model": {
                "type": "fusion",
                "params": {
                    "workers": [
                        {"type": "mock"},
                        {"type": "mock"},
                    ],
                    "policy": {"quorum": 2},
                },
            }
        }
    )

    assert isinstance(agent.provider, FusionProvider)
    assert len(agent.provider.workers) == 2
