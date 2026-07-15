"""IOS Retrieval — Exceptions."""
from __future__ import annotations

from app.core.exceptions import IosBaseException


class RetrievalError(IosBaseException):
    http_status = 500
    code = "RETRIEVAL_ERROR"


class VectorRetrievalError(RetrievalError):
    code = "VECTOR_RETRIEVAL_ERROR"


class GraphRetrievalError(RetrievalError):
    code = "GRAPH_RETRIEVAL_ERROR"


class MemoryRetrievalError(RetrievalError):
    code = "MEMORY_RETRIEVAL_ERROR"


class HybridRetrievalError(RetrievalError):
    code = "HYBRID_RETRIEVAL_ERROR"


class RerankingError(RetrievalError):
    code = "RERANKING_ERROR"


class QueryRewriteError(RetrievalError):
    code = "QUERY_REWRITE_ERROR"


class ContextBuildError(RetrievalError):
    code = "CONTEXT_BUILD_ERROR"


class ContextBudgetExceededError(ContextBuildError):
    http_status = 422
    code = "CONTEXT_BUDGET_EXCEEDED"


class RetrievalCacheError(RetrievalError):
    code = "RETRIEVAL_CACHE_ERROR"


class NoSourcesAvailableError(RetrievalError):
    http_status = 503
    code = "NO_SOURCES_AVAILABLE"


class InvalidRetrievalStrategyError(RetrievalError):
    http_status = 422
    code = "INVALID_RETRIEVAL_STRATEGY"