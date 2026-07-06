"""IOS — Services Package.

Re-exports every service and the Orchestrator::

    from app.services import AgentService, Orchestrator, BaseService
"""
from app.services.base import BaseService
from app.services.auth_service import AuthService
from app.services.user_service import UserService
from app.services.conversation_service import ConversationService
from app.services.knowledge_service import KnowledgeService
from app.services.memory_service import MemoryService
from app.services.workflow_service import WorkflowService
from app.services.tool_service import ToolService
from app.services.evaluation_service import EvaluationService
from app.services.configuration_service import ConfigurationService
from app.services.agent_service import AgentService
from app.services.orchestrator import Orchestrator

__all__ = [
    "BaseService",
    "AuthService",
    "UserService",
    "ConversationService",
    "KnowledgeService",
    "MemoryService",
    "WorkflowService",
    "ToolService",
    "EvaluationService",
    "ConfigurationService",
    "AgentService",
    "Orchestrator",
]