"""IOS Knowledge — Exceptions."""
from __future__ import annotations

from app.core.exceptions import IosBaseException


class KnowledgeError(IosBaseException):
    http_status = 500
    code = "KNOWLEDGE_ERROR"


class EntityNotFoundError(KnowledgeError):
    http_status = 404
    code = "ENTITY_NOT_FOUND"


class EntityAlreadyExistsError(KnowledgeError):
    http_status = 409
    code = "ENTITY_ALREADY_EXISTS"


class RelationNotFoundError(KnowledgeError):
    http_status = 404
    code = "RELATION_NOT_FOUND"


class RelationAlreadyExistsError(KnowledgeError):
    http_status = 409
    code = "RELATION_ALREADY_EXISTS"


class InvalidEntityLabelError(KnowledgeError):
    http_status = 422
    code = "INVALID_ENTITY_LABEL"


class InvalidRelationTypeError(KnowledgeError):
    http_status = 422
    code = "INVALID_RELATION_TYPE"


class GraphTraversalError(KnowledgeError):
    code = "GRAPH_TRAVERSAL_ERROR"


class KnowledgeSearchError(KnowledgeError):
    code = "KNOWLEDGE_SEARCH_ERROR"


class KnowledgeValidationError(KnowledgeError):
    http_status = 422
    code = "KNOWLEDGE_VALIDATION_ERROR"


class KnowledgeMergeError(KnowledgeError):
    code = "KNOWLEDGE_MERGE_ERROR"


class KnowledgeIndexError(KnowledgeError):
    code = "KNOWLEDGE_INDEX_ERROR"


class GraphConnectionError(KnowledgeError):
    http_status = 503
    code = "GRAPH_CONNECTION_ERROR"


class DuplicateEntityError(KnowledgeError):
    http_status = 409
    code = "DUPLICATE_ENTITY"


class ConfidenceTooLowError(KnowledgeError):
    http_status = 422
    code = "CONFIDENCE_TOO_LOW"