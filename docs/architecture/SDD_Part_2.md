# SECTION 8: FUNCTIONAL REQUIREMENTS

## FR-001: Agent Orchestration

### FR-001-1: Dynamic Planning
- The system SHALL accept a natural language task description and produce a structured, multi-step execution plan
- The plan SHALL identify required agents, tools, data sources, and dependencies for each step
- The plan SHALL be represented as a directed acyclic graph (DAG) with conditional branches
- The system SHALL allow plan modification by human operators before execution
- The system SHALL support plan re-generation upon step failure

### FR-001-2: Task Decomposition
- The Planner Agent SHALL decompose complex tasks into atomic subtasks, each executable by a single specialized agent
- Subtasks SHALL have defined inputs, expected outputs, success criteria, and timeout bounds
- The system SHALL support hierarchical decomposition (subtasks containing sub-subtasks)
- Dependencies between subtasks SHALL be explicitly modeled and enforced during execution

### FR-001-3: Agent Execution
- The system SHALL support parallel execution of independent subtasks
- The system SHALL support sequential execution of dependent subtasks
- Each agent SHALL have a defined capability profile, resource budget, and permission scope
- The Supervisor Agent SHALL monitor agent execution and intervene on failure or timeout

### FR-001-4: Human-in-the-Loop
- The system SHALL pause workflow execution at defined checkpoints and await human approval
- Human operators SHALL be able to review, modify, approve, or reject agent outputs at any checkpoint
- The system SHALL support configurable approval thresholds (e.g., require approval for tool calls with side effects)
- Pending approvals SHALL be persisted and survives system restarts

### FR-001-5: Streaming Execution
- Agent outputs SHALL be streamed token-by-token to connected clients via WebSocket
- Intermediate agent states SHALL be streamed as structured events
- Tool execution progress SHALL be streamed as structured events
- Clients SHALL be able to subscribe to specific agent streams or the full workflow stream

## FR-002: Memory System

### FR-002-1: Conversation Memory
- The system SHALL maintain conversation history for each session
- Conversation memory SHALL support sliding window truncation at configurable token limits
- The system SHALL auto-summarize older conversation segments when approaching token limits
- Conversation memories SHALL be associated with user sessions and persisted across disconnections

### FR-002-2: Episodic Memory
- The system SHALL record each significant task execution as an experience record
- Experience records SHALL include: task description, execution plan, steps taken, tools used, outcomes, evaluation scores, and user feedback
- Experience records SHALL be vectorized and stored for semantic retrieval
- The system SHALL retrieve relevant past experiences before executing similar tasks

### FR-002-3: Semantic Long-Term Memory
- The system SHALL maintain a semantic memory store of facts, knowledge, and learnings extracted from interactions
- Semantic memories SHALL be retrievable by semantic similarity to queries
- The system SHALL support memory creation, retrieval, update, and deletion operations
- Memory importance scores SHALL be used to prioritize retention during capacity management

### FR-002-4: Knowledge Graph Memory
- The system SHALL maintain a structured knowledge graph of entities, relationships, and attributes
- The knowledge graph SHALL support creation and traversal of entity relationships
- Graph queries SHALL support pattern matching, path finding, and aggregation
- The knowledge graph SHALL be updated automatically from agent interactions and document ingestion

### FR-002-5: Memory Management
- The system SHALL support cross-layer memory search (searching all memory types simultaneously)
- The system SHALL implement memory consolidation (promoting working memory to long-term memory)
- The system SHALL implement configurable memory decay for time-sensitive information
- The system SHALL provide a memory management API for direct CRUD operations

## FR-003: Retrieval-Augmented Generation

### FR-003-1: Document Ingestion
- The system SHALL support ingestion of PDF, DOCX, TXT, MD, HTML, and CSV files
- Ingestion SHALL extract text, tables, and (for PDF/DOCX) embedded images
- The system SHALL support URL-based document ingestion
- Ingestion SHALL produce structured document records with metadata (source, timestamp, author, tags)

### FR-003-2: Chunking
- The system SHALL support multiple chunking strategies: recursive character splitting, semantic sentence splitting, and fixed-size sliding window
- Chunk size and overlap SHALL be configurable per document type
- The system SHALL preserve document structure metadata within chunks (section headings, page numbers)
- The system SHALL assign unique identifiers to each chunk for citation tracking

### FR-003-3: Embedding
- The system SHALL support multiple embedding models (configurable per collection)
- The system SHALL batch-embed chunks for efficiency
- Embeddings SHALL be stored in Qdrant with associated metadata
- The system SHALL support embedding model versioning and re-embedding on model change

### FR-003-4: Hybrid Retrieval
- The system SHALL support BM25 sparse retrieval
- The system SHALL support dense vector retrieval from Qdrant
- The system SHALL support reciprocal rank fusion for merging retrieval results
- The system SHALL support configurable retrieval strategy per query type

### FR-003-5: Re-ranking and Compression
- The system SHALL apply cross-encoder re-ranking to retrieved candidates
- The system SHALL compute confidence scores for each retrieved chunk
- The system SHALL apply context compression to extract high-relevance spans
- Compressed context SHALL include citation metadata linking back to source documents

### FR-003-6: Hallucination Detection
- The system SHALL apply NLI-based fact verification to generated outputs
- Each factual claim SHALL be checked against retrieved context
- Claims not supported by retrieved context SHALL be flagged
- Hallucination scores SHALL be computed per response and stored in the evaluation database

## FR-004: Tool Ecosystem

### FR-004-1: Python Execution Tool
- The system SHALL execute Python code in a sandboxed environment
- Execution SHALL have configurable resource limits (CPU time, memory, file system access)
- Execution results SHALL include stdout, stderr, return value, and execution time
- The sandbox SHALL prevent access to system resources outside defined scope

### FR-004-2: SQL Execution Tool
- The system SHALL connect to configured database instances and execute SQL queries
- The system SHALL support read-only and read-write execution modes (configurable per permission level)
- Results SHALL be returned as structured data with column metadata
- The system SHALL prevent destructive operations without explicit permission

### FR-004-3: Vision and OCR Tool
- The system SHALL process image files and extract text via OCR
- The system SHALL support image analysis via local vision-capable LLMs
- The system SHALL support chart and diagram interpretation
- The system SHALL return structured results with confidence scores

### FR-004-4: File System Tool
- The system SHALL support reading and writing files within a sandboxed directory
- The system SHALL support file listing, creation, modification, and deletion
- All file operations SHALL be logged with user attribution
- File access SHALL be scoped by user permission level

### FR-004-5: Chart Generation Tool
- The system SHALL generate charts (bar, line, scatter, histogram, heatmap) from structured data
- Charts SHALL be exported as PNG, SVG, or embedded in reports
- Chart generation SHALL support customizable styling and labeling
- Generated charts SHALL be stored and accessible via URL

### FR-004-6: Report Generation Tool
- The system SHALL generate structured reports in PDF and DOCX formats
- Reports SHALL support sections, headings, tables, charts, and citations
- Report templates SHALL be configurable and version-controlled
- Generated reports SHALL be stored and accessible via download link

## FR-005: Authentication and Authorization

### FR-005-1: Authentication
- The system SHALL support JWT-based authentication with configurable token expiry
- The system SHALL support OAuth2 social login (Google, GitHub)
- The system SHALL implement refresh token rotation
- The system SHALL support API key authentication for programmatic access

### FR-005-2: Authorization
- The system SHALL implement role-based access control (RBAC)
- Roles SHALL include: Admin, Operator, Analyst, Viewer, and API Client
- Each role SHALL have a defined permission set
- Tool execution permissions SHALL be configurable per role
- Permission changes SHALL take effect immediately without restart

## FR-006: API

### FR-006-1: REST API
- The system SHALL expose a versioned REST API (v1) for all system operations
- All API responses SHALL follow a standard envelope format
- All endpoints SHALL be documented with OpenAPI 3.0 specification
- The API SHALL support pagination, filtering, and sorting for collection endpoints

### FR-006-2: WebSocket API
- The system SHALL provide WebSocket endpoints for real-time streaming
- WebSocket connections SHALL be authenticated via JWT
- The system SHALL support selective streaming (subscribe to specific workflow, agent, or session)
- WebSocket connections SHALL be resilient with heartbeat and reconnection support

---

# SECTION 9: NON-FUNCTIONAL REQUIREMENTS

## Performance Requirements

### PR-001: Response Latency
- Simple query responses: < 2 seconds end-to-end (p95)
- RAG retrieval pipeline: < 500ms for retrieval + reranking (p95)
- Agent task initiation: < 100ms from API receipt to first LLM token (p95)
- WebSocket first token: < 300ms from task submission (p95)
- Memory read (working): < 5ms (p99)
- Memory read (semantic): < 100ms (p95)
- Knowledge graph query (simple): < 50ms (p95)

### PR-002: Throughput
- The system SHALL support ≥ 100 concurrent active agent sessions
- The REST API SHALL handle ≥ 1,000 requests/second at peak load
- The WebSocket layer SHALL support ≥ 500 concurrent streaming connections
- Document ingestion SHALL process ≥ 10 MB/minute throughput

### PR-003: Model Throughput
- Ollama model serving SHALL support ≥ 5 concurrent inference requests
- Embedding generation SHALL process ≥ 10,000 chunks/minute in batch mode
- Cross-encoder reranking SHALL process ≥ 100 candidate pairs/second

## Scalability Requirements

### SR-001: Horizontal Scaling
- The FastAPI application layer SHALL be stateless and horizontally scalable
- Agent execution SHALL be distributable across multiple worker nodes
- Redis SHALL serve as the shared state backend enabling stateless scaling
- Database connections SHALL use connection pooling with configurable pool sizes

### SR-002: Data Volume
- The system SHALL support ≥ 10 million vector embeddings in Qdrant
- The system SHALL support ≥ 100 million knowledge graph triples in Neo4j
- The system SHALL support ≥ 1TB of document storage
- PostgreSQL SHALL support ≥ 100 million audit log records with archival

### SR-003: Memory Scaling
- Agent working memory SHALL scale with conversation length without performance degradation
- Long-term memory retrieval latency SHALL remain < 200ms up to 50 million memory records
- Knowledge graph query performance SHALL be maintained through appropriate indexing

## Availability Requirements

### AR-001: Uptime
- Target system availability: 99.5% (allows ~44 hours/year downtime)
- Planned maintenance windows: < 2 hours/month
- Core intelligence kernel: zero tolerance for unplanned downtime
- Database tier: 99.9% availability target

### AR-002: Fault Tolerance
- The system SHALL continue operating with degraded (not zero) capability if any single service fails
- If Qdrant is unavailable, the system SHALL fall back to BM25-only retrieval
- If Neo4j is unavailable, the system SHALL continue with reduced memory capabilities
- If an agent fails, the Supervisor SHALL retry or reroute to an equivalent agent

### AR-003: Recovery
- Recovery Time Objective (RTO): < 15 minutes for full system recovery
- Recovery Point Objective (RPO): < 5 minutes data loss tolerance
- Automated health checks SHALL detect failures within < 30 seconds
- Automated restarts SHALL be triggered for failed containers

## Reliability Requirements

### RR-001: Data Consistency
- All database writes SHALL be transactional where applicable
- Event sourcing SHALL be used for audit-critical operations
- Memory operations SHALL be idempotent where possible
- The system SHALL implement optimistic locking for concurrent memory writes

### RR-002: Agent Reliability
- Every agent execution SHALL be wrapped in retry logic with exponential backoff
- Maximum retry attempts SHALL be configurable per agent type
- Failed agent executions SHALL be logged with full context for debugging
- Circuit breakers SHALL prevent cascade failures in agent chains

## Maintainability Requirements

### MR-001: Code Quality
- All Python code SHALL pass Ruff linting with zero errors
- All Python code SHALL pass MyPy strict type checking
- Code coverage SHALL be ≥ 80% for all modules
- All public functions and classes SHALL have complete docstrings

### MR-002: Dependency Management
- All dependencies SHALL be pinned to exact versions
- Dependencies SHALL be regularly audited for security vulnerabilities (automated)
- The system SHALL use a single virtual environment managed by Poetry or pip-tools
- Dependency updates SHALL go through CI/CD review before merging

### MR-003: Documentation
- All API endpoints SHALL be documented in OpenAPI specification
- Architecture decisions SHALL be documented in ADR (Architecture Decision Records) format
- All configuration parameters SHALL be documented with types, defaults, and descriptions
- Operations runbooks SHALL be maintained for all deployment and incident procedures

## Security Requirements

### SEC-001: Authentication
- All API endpoints (except /health and /auth/login) SHALL require authentication
- JWT tokens SHALL expire after a configurable duration (default: 15 minutes)
- Refresh tokens SHALL expire after 7 days and be rotated on use
- Failed authentication attempts SHALL be rate-limited and logged

### SEC-002: Authorization
- All resource access SHALL be verified against user permissions before execution
- Tool execution SHALL require explicit permission per tool per role
- Admin operations SHALL require admin role verification
- Cross-user data access SHALL be prevented at the data access layer

### SEC-003: Input Security
- All user inputs SHALL be sanitized before use in prompts (prompt injection prevention)
- SQL queries generated by agents SHALL be parameterized, never string-concatenated
- File paths SHALL be validated against allowed directories before access
- Code execution inputs SHALL be scanned for known malicious patterns

### SEC-004: Data Security
- Sensitive configuration values SHALL be stored in environment variables, never in code
- Database connections SHALL use encrypted TLS connections
- API keys and secrets SHALL be hashed before storage
- PII data SHALL be identified and handled per data classification policy

### SEC-005: Network Security
- All external traffic SHALL be routed through NGINX with TLS termination
- Internal service communication SHALL use mTLS in production
- Rate limiting SHALL be enforced at the NGINX layer (100 req/min default per IP)
- CORS policy SHALL be explicitly configured (no wildcard in production)

### SEC-006: Audit
- All authentication events SHALL be logged with IP, timestamp, and outcome
- All tool executions SHALL be logged with user, parameters, and outcome
- All data modification operations SHALL be logged with before/after state
- Audit logs SHALL be immutable (append-only) and tamper-evident

## Business Requirements

### BR-001: Open Source Compliance
- All third-party dependencies SHALL use OSI-approved open source licenses
- All Ollama models SHALL be used in compliance with their respective licenses
- The project SHALL be released under an OSI-approved license (MIT or Apache 2.0)
- All AI-generated code in the repository SHALL be disclosed as such

### BR-002: Portability
- The system SHALL run on Linux, macOS, and Windows (via Docker)
- The system SHALL support AMD64 and ARM64 architectures
- All hardware requirements SHALL be met by consumer-grade hardware (minimum 16GB RAM)
- A complete local development environment SHALL be configurable with a single command

### BR-003: Documentation Quality
- The README SHALL enable a new engineer to run the system within 30 minutes
- All architectural decisions SHALL be documented with rationale
- Contribution guidelines SHALL be clear and complete
- The API documentation SHALL include request/response examples for every endpoint

---

# SECTION 10: COMPLETE TECHNOLOGY STACK

## Backend Runtime

### Python 3.12
**Why:** Python 3.12 introduces significant performance improvements (5-15% speedup over 3.11), improved error messages for debugging, and performance optimizations to the interpreter. It is the most recent stable Python release and ensures access to the latest async improvements.
**Alternatives Considered:** Python 3.11 (stable but older), Go (faster but lacks AI/ML ecosystem depth), Rust (fastest but prohibitive ML ecosystem friction).
**Tradeoffs:** Python's GIL limits true parallelism; mitigated by asyncio's event-loop model and subprocess workers for CPU-bound tasks.

### FastAPI
**Why:** FastAPI is the industry standard for high-performance Python async web APIs. It provides automatic OpenAPI documentation generation, native Pydantic integration, excellent WebSocket support, and a dependency injection system ideal for Clean Architecture.
**Alternatives Considered:** Django REST Framework (synchronous-first, heavier ORM coupling), Flask (no native async, minimal structure), Starlette (FastAPI's base; too low-level for production APIs).
**Tradeoffs:** Steeper learning curve than Flask; dependency injection requires discipline; Pydantic v2 migration breaking changes require attention.

## Agent Orchestration

### LangGraph
**Why:** LangGraph provides the most mature graph-based agent orchestration primitive in the Python ecosystem. Its state machine model maps directly to the cognitive architecture of multi-agent systems — nodes are agents, edges are transitions, and state is the shared intelligence context. It supports streaming, interrupts, human-in-the-loop, and subgraph composition.
**Alternatives Considered:** LangChain LCEL (sequential chains, not graphs), AutoGen (conversation-centric, not graph-centric), custom orchestration (prohibitive engineering cost).
**Tradeoffs:** LangGraph is relatively new and APIs may evolve; its state management model requires careful design to avoid state explosion.

### LangChain
**Why:** LangChain provides the foundational abstractions (LLM interfaces, prompt templates, output parsers, document loaders, text splitters) that IOS builds upon. It is not used as a monolithic framework but as a component library.
**Alternatives Considered:** Direct SDK calls (brittle, non-portable), Haystack (less flexible, Django-like coupling).
**Tradeoffs:** LangChain has historically been criticized for complexity and abstraction leakage; IOS uses it selectively at the integration layer only.

## Local Model Serving

### Ollama
**Why:** Ollama is the most user-friendly and production-capable local LLM serving solution. It provides a Docker-compatible API, model management (pull, update, delete), multi-model support, GPU acceleration, and an OpenAI-compatible REST interface — enabling seamless integration with LangChain.
**Alternatives Considered:** llama.cpp (faster but no HTTP server), vLLM (requires NVIDIA GPU, not universal), LM Studio (GUI-only, no API in free tier), Hugging Face Inference Endpoints (cloud, not local).
**Tradeoffs:** Ollama adds ~200ms overhead per model load for cold starts; mitigated by model preloading and keep-alive configuration.

## Vector Infrastructure

### Qdrant
**Why:** Qdrant is a purpose-built, production-grade vector database written in Rust with Python, REST, and gRPC interfaces. It provides: quantization for efficient storage, filtering with payload conditions (enabling hybrid filtering during vector search), sparse vector support (critical for BM25 hybrid retrieval), named vectors (enabling multi-embedding-model collections), and excellent documentation.
**Alternatives Considered:** Pinecone (cloud-only, cost at scale), Weaviate (heavier runtime, GraphQL-centric), Chroma (excellent for development, less production-ready), Milvus (strong but operationally complex), pgvector (PostgreSQL extension, sufficient for small scale but doesn't scale to millions of vectors with sub-100ms latency).
**Tradeoffs:** Qdrant requires separate deployment; payload filtering can be complex for dynamic schemas.

## Graph Database

### Neo4j
**Why:** Neo4j is the industry-standard graph database with the most mature Cypher query language, excellent Python driver (neo4j-driver), native graph algorithms, and a rich ecosystem. For knowledge graph operations — entity resolution, path finding, relationship traversal, subgraph extraction — Neo4j is unmatched.
**Alternatives Considered:** Apache AGE (PostgreSQL extension, less mature), Amazon Neptune (cloud-only), TigerGraph (enterprise licensing), ArangoDB (multi-model, less optimized for pure graph).
**Tradeoffs:** Neo4j Community Edition has limitations (single instance); the enterprise version requires licensing. For this project, community edition is sufficient.

## Relational Database

### PostgreSQL 16
**Why:** PostgreSQL is the most feature-complete open-source relational database, with excellent support for JSONB (flexible document storage within relational schema), full-text search, advanced indexing (GiST, GIN, BRIN), table partitioning, and a robust ecosystem of extensions. All structured metadata — users, sessions, audit logs, configurations, evaluation results — resides in PostgreSQL.
**Alternatives Considered:** MySQL (less feature-complete JSONB, weaker transaction isolation), SQLite (no concurrent write support), CockroachDB (distributed PostgreSQL, overkill for V1).
**Tradeoffs:** PostgreSQL requires more operational attention than SQLite; connection pooling (via PgBouncer or SQLAlchemy pool) is essential at scale.

### SQLAlchemy 2.0
**Why:** SQLAlchemy 2.0 introduces native async support (asyncpg driver), a completely redesigned Core/ORM API with better type safety, and improved performance. It is the definitive Python ORM for production applications.
**Alternatives Considered:** Tortoise ORM (async-native but less mature), Databases (too low-level), raw asyncpg (no ORM, high boilerplate).
**Tradeoffs:** SQLAlchemy 2.0 has breaking changes from 1.x; async session management requires careful context management.

### Alembic
**Why:** Alembic is the standard SQLAlchemy migration tool, providing auto-generation of migration scripts from model changes, branching support, and rollback capability.
**Alternatives Considered:** Django migrations (tied to Django), Flyway (Java ecosystem), raw SQL scripts (no automation).
**Tradeoffs:** Alembic auto-generation requires review; generated migrations can miss edge cases for complex schema changes.

## Cache and Message Bus

### Redis 7
**Why:** Redis 7 is used as: (1) the working memory backend for agent sessions, (2) the caching layer for LLM responses and embeddings, (3) the message bus for inter-service communication (via Pub/Sub), (4) the rate limiter (via Redis cell/sliding window counters), and (5) the task queue backend (via Redis Streams or RQ).
**Alternatives Considered:** Memcached (no persistence, no pub/sub), Apache Kafka (overkill for internal messaging at this scale), RabbitMQ (AMQP overhead for simple messaging).
**Tradeoffs:** Redis is single-threaded; very large values can block other operations. Redis Cluster needed for horizontal scaling.

## ML and DL Frameworks

### PyTorch 2.x
**Why:** PyTorch is the dominant deep learning framework in research and increasingly in production. It provides the most flexible neural network building primitives, excellent debugging experience, and native ONNX export for model deployment.
**Alternatives Considered:** TensorFlow (more complex, declining research adoption), JAX (excellent but steeper learning curve, less production tooling).
**Tradeoffs:** PyTorch models require conversion for efficient serving; addressed via ONNX export and TorchScript.

### HuggingFace Transformers + Datasets + Hub
**Why:** HuggingFace provides the largest repository of pre-trained models, including the Sentence Transformers used for embedding, the cross-encoders used for reranking, and various specialized models. The Datasets library provides efficient data loading for fine-tuning pipelines.
**Alternatives Considered:** Direct model implementation (prohibitive), vendor SDKs (lock-in).
**Tradeoffs:** HuggingFace models can be large; model caching and lazy loading essential.

### Sentence Transformers
**Why:** Sentence Transformers provides the most production-ready implementations of semantic embedding models (BGE, E5, all-MiniLM, etc.) with efficient batching, device management, and the SentenceTransformer API.
**Alternatives Considered:** OpenAI Ada embeddings (cloud dependency, cost), Cohere Embed (cloud dependency), raw HuggingFace (more code, less optimization).
**Tradeoffs:** Local embedding models are slower than cloud APIs but have zero cost and zero latency variance.

### Scikit-learn, XGBoost, CatBoost, LightGBM
**Why:** These four libraries cover the essential gradient boosting and classical ML landscape. XGBoost for GPU-accelerated gradient boosting; CatBoost for excellent categorical feature handling; LightGBM for fastest training on large datasets; Scikit-learn for preprocessing, pipelines, and classical algorithms.
**Alternatives Considered:** Only using one GBDT library (less flexibility), pure neural approaches (overkill for structured data tasks like intent classification).
**Tradeoffs:** Managing four similar libraries adds complexity; addressed by unified ML pipeline interface.

### Optuna
**Why:** Optuna is the state-of-the-art Python hyperparameter optimization framework with efficient TPE (Tree-structured Parzen Estimator) sampling, pruning of unpromising trials, distributed optimization support, and excellent MLflow integration.
**Alternatives Considered:** Ray Tune (heavier Ray dependency), Hyperopt (older, less maintained), Scikit-Optimize (limited parallelism).
**Tradeoffs:** Optuna trials can be expensive; addressed by pruning callbacks and resource budgets.

### MLflow
**Why:** MLflow provides experiment tracking, model registry, and artifact storage for all ML/DL experiments. Every model training run, evaluation result, and prompt performance experiment is tracked in MLflow.
**Alternatives Considered:** Weights & Biases (cloud-first, cost at scale), Neptune.ai (cloud-first), custom logging (no standard interface).
**Tradeoffs:** MLflow's UI is functional but not beautiful; metric visualization less capable than W&B. Addressed by Grafana integration.

## Observability Stack

### OpenTelemetry
**Why:** OpenTelemetry is the CNCF-standard observability framework providing vendor-neutral distributed tracing, metrics, and logging. Every LLM call, agent execution, tool invocation, retrieval operation, and database query is instrumented with OTel spans.
**Alternatives Considered:** Jaeger-native SDK (vendor lock-in), Datadog APM (cost), custom tracing (no standards compliance).
**Tradeoffs:** OTel adds minimal overhead (~1ms per span); instrumentation requires discipline to maintain.

### Prometheus
**Why:** Prometheus is the industry-standard metrics collection and alerting system. IOS exposes /metrics endpoints from every service, and Prometheus scrapes them for storage, alerting, and Grafana visualization.
**Alternatives Considered:** InfluxDB (time-series DB, less native Prometheus integration), Datadog (cost, cloud).
**Tradeoffs:** Prometheus pull model requires network accessibility of all services; addressed by Docker network configuration.

### Grafana
**Why:** Grafana is the industry-standard observability dashboard platform, supporting Prometheus, PostgreSQL, Loki, and custom data sources. IOS ships pre-configured Grafana dashboards for agent performance, retrieval quality, system resources, and ML experiment metrics.
**Alternatives Considered:** Kibana (ELK stack, heavier), Datadog (cost), custom dashboards (high maintenance).
**Tradeoffs:** Grafana requires dashboard maintenance as metrics evolve.

## Frontend

### Next.js 14 (App Router)
**Why:** Next.js 14 with the App Router provides: React Server Components for efficient server-side rendering, streaming SSR for progressive content loading, built-in API routes, excellent TypeScript support, and the most mature production React deployment story.
**Alternatives Considered:** Remix (excellent but smaller ecosystem), Create React App (deprecated), Vite SPA (no SSR, inferior performance for content-heavy pages).
**Tradeoffs:** Next.js App Router is relatively new; some React ecosystem libraries not yet RSC-compatible.

### TypeScript 5.x
**Why:** TypeScript is non-negotiable for production frontend code. Strict typing eliminates entire classes of runtime errors, enables excellent IDE tooling, and makes large codebases maintainable.
**Alternatives Considered:** JavaScript (no type safety, not appropriate for enterprise code).
**Tradeoffs:** TypeScript adds compilation step and requires type definitions for all libraries.

### TailwindCSS
**Why:** TailwindCSS provides utility-first CSS that eliminates style conflict, reduces CSS bundle size through purging, and enables rapid UI development without context-switching to CSS files.
**Alternatives Considered:** CSS Modules (more verbose), Styled Components (runtime overhead), MUI (opinionated design system, harder to customize).
**Tradeoffs:** Tailwind classes can make JSX verbose; addressed by component extraction.

### Shadcn/ui
**Why:** Shadcn/ui provides a collection of beautifully designed, accessible, copy-paste React components built on Radix UI primitives and styled with Tailwind. Unlike component libraries, shadcn/ui components are copied into the project and fully customizable.
**Alternatives Considered:** Radix UI (lower-level, more work), MUI (heavier, opinionated), Ant Design (less modern aesthetic).
**Tradeoffs:** Copy-paste model means updates to shadcn require manual review.

### Framer Motion
**Why:** Framer Motion provides production-quality animations with a simple declarative API. Agent activity visualizations, streaming text rendering, and workflow graph animations require smooth, performant motion that Framer Motion excels at.
**Alternatives Considered:** React Spring (more complex API), CSS transitions (insufficient for complex animations), GSAP (heavy, not React-native).
**Tradeoffs:** Framer Motion adds ~50KB to bundle; tree-shaking partially mitigates this.

### React Query (TanStack Query v5)
**Why:** TanStack Query provides declarative server state management with caching, background refetching, optimistic updates, and excellent DevTools. It eliminates manual loading/error/data state management throughout the UI.
**Alternatives Considered:** SWR (less featured than React Query), RTK Query (Redux coupling), manual fetch+useState (excessive boilerplate).
**Tradeoffs:** TanStack Query v5 has API changes from v4; requires migration discipline.

## Infrastructure

### Docker
**Why:** Docker is the universal containerization standard. Every IOS service is containerized with multi-stage builds for minimal image size, non-root users for security, and health checks for orchestration.
**Alternatives Considered:** Podman (excellent alternative, less ecosystem), VMs (too heavyweight), bare metal (non-portable).
**Tradeoffs:** Docker introduces image management overhead; addressed by automated build pipelines.

### Docker Compose
**Why:** Docker Compose is the standard for local and staging multi-service deployment. IOS's compose configuration defines all services, networks, volumes, and health check dependencies, enabling a complete local environment with a single command.
**Alternatives Considered:** Kubernetes (appropriate for production at scale, planned V2), Helm (Kubernetes-specific), Docker Swarm (less maintained).
**Tradeoffs:** Docker Compose is not production-grade at scale; addressed by planned Kubernetes migration in V2.

### NGINX
**Why:** NGINX serves as the production reverse proxy providing TLS termination, rate limiting, request routing to FastAPI services, static file serving for Next.js, WebSocket proxying, and a security header injection layer.
**Alternatives Considered:** Traefik (excellent but more complex configuration), Caddy (simpler but less production battle-tested at scale), HAProxy (layer 4, less HTTP feature-rich).
**Tradeoffs:** NGINX configuration is not trivial; addressed by version-controlled, documented configuration.

### GitHub Actions
**Why:** GitHub Actions provides native CI/CD integration with the repository, with a large marketplace of actions for common tasks, free usage for open source repositories, and matrix build support for multi-platform testing.
**Alternatives Considered:** GitLab CI (requires GitLab hosting), Jenkins (operational overhead), CircleCI (cost), Buildkite (cost).
**Tradeoffs:** GitHub Actions minutes have limits for private repositories; managed within project budget.