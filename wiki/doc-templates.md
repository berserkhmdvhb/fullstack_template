# Documentation and Docstring Convention

This page defines the docstring convention for **this service template** (the `your_service_name` package). It is deliberately lightweight: document **public API** (re-exported symbols, HTTP route modules, services, schemas) thoroughly, keep internal helpers short, and describe **behavior and contracts** rather than implementation. The section style below (reST-style underlined headings, as used across the backend) is the house format; include the sections that materially help a consumer of the symbol, skip the rest.

> **Scope & Intent**
>
> *   Prioritize **public API**: re-exports, `__init__.py` indexes, HTTP route modules, services, schemas.
> *   Internal helpers may use **reduced docstrings** (summary-only or summary + brief Notes).
> *   Prefer **type hints** in signatures; repeat types in docstrings only when it improves clarity.

***

## 0) General Principles

*   **Summary line**: first line is one concise sentence in **imperative mood** (“Return…”, “Create…”).
*   **Consistency**: use the reST-style underlined section headings shown here for richer docs; plain prose summaries are fine everywhere.
*   **Section order** (when applicable):  
    **Summary → Extended Summary → Parameters → Returns/Yields → Raises →**  
    **(Optional: Examples / Notes / Side Effects / Concurrency / Security / Performance / See Also).**
*   **Doctest-friendly** examples: prefer runnable snippets users can copy/paste.
*   **Avoid redundancy**:
    *   Don't restate defaults shown in the signature unless it clarifies behavior.
    *   Don't duplicate per-function details in the module docstring.
*   **Document behavior & contracts**, *not* implementation details likely to change.
*   **Public vs internal**:
    *   Public API (re-exports and endpoint handlers): full templates.
    *   Private helpers (`_name`): one-liner summary + concise Notes is fine.

***

## 1) Module Docstrings

**Where**: very top of each `.py` file.  
**Goal**: explain responsibilities, list public entry points, and note operational concerns. For **route modules** (FastAPI), summarize HTTP contracts and response models.

```python
"""
<Short summary of the module.>

Responsibilities
----------------
- <Responsibility 1>
- <Responsibility 2>

Public API
----------
- `<function_or_class>`: <short description>
- `<endpoint_or_router>`: <short description>

API Contract (Optional, for HTTP route modules)
-----------------------------------------------
- <Shared semantics for this route group: status codes, headers, timeouts, auth>

Response Models (Optional, for HTTP route modules)
--------------------------------------------------
- ``<ModelName>`— <when it is returned and why>
- ``<ModelName>`— <when it is returned and why>

Operational
-----------
- Concurrency: <threading/async/background tasks summary>
- Timeouts: <module-wide policy if applicable>
- Logging: <levels / structured logging / PII redaction>
- I/O: <network/disk usage if relevant>

Usage
-----
    from <package>.<module> import <symbol>
    <symbol>(...)

Notes
-----
- <Important caveats, cross-cutting concerns, or migration notes>

Security Considerations (Optional)
----------------------------------
- <Threat model assumptions; input validation; secrets handling>

Performance Notes (Optional)
----------------------------
- <Complexity; caching; batching; parallelism>

See Also (Optional)
-------------------
- <related_module_or_function>: <one-line purpose>
"""
```

**When to include optional module sections**

*   **API Contract** & **Response Models** → only for HTTP route modules.
*   **Security** → modules handling secrets/tokens/untrusted input.
*   **Performance** → modules doing heavy I/O, parsing, or concurrency.

***

## 2) Package-Level `__init__.py` (Public API Index)

Use this to explain what the **package** exposes and to maintain explicit re-exports.

```python
"""
<Package short summary (imperative; one sentence).>

Responsibilities
----------------
- <Responsibility 1>
- <Responsibility 2>

Public API
----------
Re-exported for convenience (import from this package instead of submodules):
- ``<SymbolName>`— <short description>
- ``<SymbolName>`— <short description>

Usage
-----
    from <root_pkg>.<this_pkg> import <SymbolName>
    <SymbolName>(...)

Notes
-----
- Keep ``__all__`aligned with *Public API*; avoid re-exporting unstable internals.
"""
```

```python
# __init__.py skeleton
from __future__ import annotations
# from .<module> import <SymbolName>
__all__: list[str] = [
    # "<SymbolName>",
]
```

***

## 3) Class Docstrings

**Where**: public classes (models, services, managers, exceptions).  
**Goal**: purpose, usage, key attributes; document `__init__` parameters if meaningful.

```python
class ClassName:
    """
    <One-sentence summary of the class purpose.>

    Extended Summary
    ----------------
    - <Key responsibility or design intent>
    - <Lifecycle or usage constraints>

    Parameters (Include if __init__ has meaningful arguments)
    ---------------------------------------------------------
    param1 : Type
        Description.
    param2 : Type, optional
        Description; default behavior if omitted.

    Attributes
    ----------
    attr_name : Type
        Description and invariants.
    attr_name : Type
        Description and invariants.

    Concurrency (Optional)
    ----------------------
    - <Thread/async safety; shareability across tasks/threads>

    Side Effects (Optional)
    -----------------------
    - <External writes, background tasks, logging>

    Security Considerations (Optional)
    ----------------------------------
    - <Sensitive fields, sanitization, PII concerns>

    Performance Notes (Optional)
    ----------------------------
    - <Cost characteristics, caching, batching>

    Examples
    --------
    >>> obj = ClassName(...)
    >>> obj.method(...)
    """
```

***

## 4) Function / Method Docstrings (reST)

**Where**: public functions/methods. Internal helpers may be shorter.  
**Goal**: behavior, inputs/outputs, failure modes, important side effects/contracts.

```python
def function_name(arg1: Type, arg2: Type = default) -> ReturnType:
    """
    <Summary of what this function does.>

    Parameters
    ----------
    arg1 : Type
        Description.
    arg2 : Type, optional
        Description; defaults to <default> (only if it clarifies behavior).

    Returns
    -------
    ReturnType
        Description of the returned value and invariants.

    Raises
    ------
    ExceptionType
        Condition that triggers this exception.

    Side Effects (Optional)
    -----------------------
    - <Writes to store / disk / network; logging; background tasks>

    Concurrency (Optional)
    ----------------------
    - <Thread-safety; reentrancy; caller requirements (e.g., must be awaited)>

    Security Considerations (Optional)
    ----------------------------------
    - <Input validation expectations; sanitization; secrets handling>

    Performance Notes (Optional)
    ----------------------------
    - <Complexity; batching; caching implications>

    Examples
    --------
    >>> function_name(example)
    expected_output

    Notes
    -----
    - Additional details for edge cases or design choices.
    - related_function : Brief description
    """
```

**When to add optional sections**

*   **Side Effects** → I/O, logging, mutations, spawning tasks.
*   **Concurrency** → async-only usage, locking, non-thread-safe code.
*   **Security** → user input, URLs/HTML, credentials.
*   **Performance** → hot paths, N×M loops, heavy calls.

***

## 5) Async Functions

Highlight **cancellation** and **timeout** behavior.

```python
async def async_function(...):
    """
    <Short summary of what this async function does.>

    Cancellation & Timeouts
    -----------------------
    - Cancellation-aware; propagates `asyncio.CancelledError`.
    - <If applicable> Wrapped by `asyncio.wait_for` with a <computed/fixed> timeout.
    - <If applicable> Uses cooperative cancellation via an event/token.

    Parameters
    ----------
    ...

    Returns
    -------
    ...

    Raises
    ------
    asyncio.CancelledError
        If cancelled.
    TimeoutError
        If an internal `wait_for` times out (when applicable).

    Side Effects (Optional)
    -----------------------
    - <I/O, logging, mutations, task scheduling>

    Concurrency (Optional)
    ----------------------
    - <Caller expectations; shared-resource access>
    """
```

***

## 6) Generators

Use `Yields` instead of `Returns`.

```python
def generator_function(...) -> Iterator[Type]:
    """
    <Short summary of what this generator yields.>

    Yields
    ------
    Type
        Description of each yielded element.

    Raises (Optional)
    -----------------
    ExceptionType
        If <condition> during iteration.

    Notes (Optional)
    ----------------
    - <Back-pressure behavior; buffering; resource ownership>
    """
```

***

## 7) Context Managers

Document **enter/exit semantics** and resource ownership.

```python
class ContextManager:
    """
    <Purpose and the resource it manages.>

    Enter/Exit Semantics
    --------------------
    - `__enter__`: <what is returned; resource state>
    - `__exit__`: <what is cleaned up; exception handling guarantees>

    Parameters (Optional)
    ---------------------
    param : Type
        If `__init__` takes meaningful arguments.

    Concurrency (Optional)
    ----------------------
    - <Thread/async safety of entering/exiting concurrently>

    Examples
    --------
    >>> with ContextManager() as resource:
    ...     resource.use()
    """
```

***

## 8) Properties

Keep it crisp; clarify cached vs computed and any invalidation rules.

```python
@property
def name(self) -> Type:
    """
    <Description of what the property represents.>

    Notes
    -----
    - <Cached or computed?> <Cost?> <Invalidation rules?>
    """
```

***

## 9) Exceptions

Explain **intent** and fields; show how callers can use attributes.

```python
class CustomError(Exception):
    """
    Raised when <condition> occurs.

    Parameters
    ----------
    message : str
        Error description.
    code : int, optional
        Error code.

    Attributes (Optional)
    ---------------------
    code : int
        Accessible after construction.
    """
```

***

## 10) Deprecation

Be explicit and actionable; include removal plan and migration hint.

```python
def old_function(...):
    """
    [DEPRECATED] Use `new_function` instead.

    Raises
    ------
    DeprecationWarning
        Always emitted.

    Notes
    -----
    - Removal planned in <version/date>.
    - Migration: <one-liner or link>.
    """
```

***

## 11) Optional Sections — Quick Reference

> Include **only** when they materially help comprehension. Aim for clarity, not verbosity.

*   **API Contract** *(route modules only)*: shared HTTP semantics, status codes, headers.
*   **Response Models** *(route modules only)*: envelope types and when they're returned.
*   **Side Effects** *(functions/classes)*: I/O, logging, background tasks, mutations.
*   **Concurrency** *(modules/classes/functions)*: thread safety, async expectations, locks.
*   **Cancellation & Timeouts** *(async functions)*: how cancellation/timeout is handled.
*   **Security Considerations**: input validation, escaping, secrets handling, PII.
*   **Performance Notes**: complexity, hot paths, caching/batching, memory/latency.
*   **Enter/Exit Semantics** *(context managers)*: lifecycle & error handling.
*   **See Also**: nearby or alternative APIs worth considering.
*   **Warnings**: footguns users must notice.
*   **Contracts**: preconditions/postconditions invariant to implementation.
*   **Complexity**: time/space complexity when it matters for API users.

***

## 12) Practical Guidance for This Codebase

*   **Route modules** (`api/routers/v*/...`):  
    Include *Responsibilities*, *Public API*, *API Contract* (recommended), *Response Models* (recommended), and *Operational*.
*   **Helper modules**:  
    Lighter docstrings—*Responsibilities*, *Public API* (if any), *Operational*. Add *Security*/*Performance* if relevant.
*   **Data/DTO modules** (schemas, light models):  
    Short module docstring + thorough **class** docstrings with **Attributes**/**Examples**.
*   **Async workflows**:  
    Always include **Cancellation & Timeouts**, consider **Side Effects**/**Concurrency**.

***

## 13) Style Notes & Conventions

*   **Grammar**: present tense; imperative mood for summaries (“Return…”, “Validate…”).
*   **Pronouns**: avoid “we/you”; focus on the code's behavior.
*   **Formatting**:
    *   Use **double backticks** `like this` for inline code in reST docstrings.
    *   Use **fenced code blocks** in Markdown files; in docstrings, prefer reST code blocks when needed.
*   **Cross-references**:
    *   Use backticks for symbols in docstrings (e.g., `ClassName.method`).
    *   In Markdown docs, link to the relevant `wiki/` page; do not restate it.
*   **Line length**: keep within the project's linter limit (e.g., 100 chars).
*   **Minimalism for internals**: private helpers can be summary-only + brief Notes.

***

## 14) Example: Putting It All Together (Route Module)

```python
"""
Manage 'foo' endpoints (create and list).

Responsibilities
----------------
- Define HTTP endpoints for creating a Foo and listing Foos.
- Validate requests and serialize responses via Pydantic schemas.
- Delegate business rules to a service layer and persistence to a repository.

Public API
----------
- `POST /api/v1/foo` : Create a new Foo and return it (201 Created).
- `GET  /api/v1/foo` : List previously created Foos (200 OK).

API Contract
------------
- `POST /foo` returns HTTP 201 and sets a ``Location`header pointing to ``/api/v1/foo``.
- Error semantics:
  - 422 Unprocessable Entity on validation failure (Pydantic/FastAPI).
- Security headers are applied globally by middleware; CORS is configured in app settings.

Response Models
---------------
- ``FooResponse`— returned by POST on success (201) and in listings (200).
- ``FooList`— returned by GET listings (200) as a simple container.

Operational
-----------
- Concurrency: endpoint handlers are async; demo repository is in-memory and not thread-safe.
- Logging: routers keep logging minimal; services may log business decisions.
- I/O: the demo repository is purely in-memory (no external I/O).

Usage
-----
    from your_service_name.backend.api.routers.v1 import foo
    app.include_router(foo.router, prefix="/api/v1")

Notes
-----
- When mounted under `/api/<version>`, this router is reachable at `/api/<version>/foo`.
- In production, place schemas/models/services/repos in their respective packages and
  wire the service instance in app lifespan (see `get_foo_service` below).

Security Considerations
-----------------------
- User-supplied `name` is validated by Pydantic; avoid logging sensitive inputs verbatim.
- No authentication is applied in this demo; add auth dependencies as required.

Performance Notes
-----------------
- The in-memory repository is for demos only (O(N) materialization on list).
- Use a database-backed repository for persistence and large datasets.

See Also
--------
- ``your_service_name.backend.services.foo_service.FooService`— business logic
- ``your_service_name.backend.repositories.foo_repo`— persistence contracts
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────────────
# Schemas - request/response
# ──────────────────────────────────────────────────────────────────────────────


class FooRequest(BaseModel):
    """Client payload to create a Foo."""

    name: str = Field(min_length=1, description="Human-readable name of the Foo.")


class FooResponse(BaseModel):
    """Server representation of a Foo."""

    name: str
    length: int


class FooList(BaseModel):
    """Container for listing Foos."""

    items: list[FooResponse]


# ──────────────────────────────────────────────────────────────────────────────
# Domain model
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Foo:
    """Domain entity representing a Foo."""

    name: str

    def length(self) -> int:
        """Return the number of characters in ``name``."""
        return len(self.name)


# ──────────────────────────────────────────────────────────────────────────────
# Repository contract + in-memory implementation (demo)
# ──────────────────────────────────────────────────────────────────────────────


class FooRepository(Protocol):
    """Persistence contract for storing and listing `Foo` entities."""

    def save(self, foo: Foo) -> None: ...
    def list(self) -> Iterable[Foo]: ...


class InMemoryFooRepository:
    """Demo in-memory repository (not for production use)."""

    def __init__(self) -> None:
        self._items: list[Foo] = []

    def save(self, foo: Foo) -> None:
        """Append the entity to memory (no deduplication)."""
        self._items.append(foo)

    def list(self) -> Iterable[Foo]:
        """Return a shallow copy of all items."""
        return list(self._items)


# ──────────────────────────────────────────────────────────────────────────────
# Service (use cases)
# ──────────────────────────────────────────────────────────────────────────────


class FooService:
    """
    Application service encapsulating Foo use-cases.

    Parameters
    ----------
    repo : FooRepository
        Persistence interface used to store and retrieve Foos.

    Concurrency (Optional)
    ----------------------
    - Stateless; instances may be shared across requests if the repository
      implementation is thread-safe/async-safe.
    """

    def __init__(self, repo: FooRepository) -> None:
        self.repo = repo

    def create(self, name: str) -> Foo:
        """
        Create and persist a new Foo.

        Parameters
        ----------
        name : str
            The Foo name (callers should pass a non-empty string).

        Returns
        -------
        Foo
            The created domain entity.

        Side Effects
        ------------
        - Persists the new Foo via the configured repository.
        """
        foo = Foo(name=name)
        self.repo.save(foo)
        return foo

    def list(self) -> list[Foo]:
        """
        List existing Foos.

        Returns
        -------
        list[Foo]
            All entities held by the repository (ordering unspecified).
        """
        return list(self.repo.list())


# ──────────────────────────────────────────────────────────────────────────────
# DI provider
# In production, construct the service during app lifespan:
# `app.state.foo_service = FooService(InMemoryFooRepository() or DbFooRepository(...))`
# and have the provider return it from request.app.state to avoid re-creating it.
# For this demo, we keep a module-level singleton for simplicity.
# ──────────────────────────────────────────────────────────────────────────────

_service_singleton = FooService(InMemoryFooRepository())


def get_foo_service() -> FooService:
    """
    Return the FooService instance (wired by lifespan in production).

    Notes
    -----
    - In real deployments, create the service in the app lifespan and
      return it from `request.app.state` to avoid per-request construction.
    """
    return _service_singleton


# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/foo", tags=["foo"])


@router.post(
    "",
    response_model=FooResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_foo(
    payload: FooRequest,
    svc: Annotated[FooService, Depends(get_foo_service)],
    response: Response,
) -> FooResponse:
    """
    Create a new Foo and return it.

    Parameters
    ----------
    payload : FooRequest
        Client payload (validated by Pydantic).
    svc : FooService
        Injected service (via DI `Depends(get_foo_service)`).
    response : fastapi.Response
        Used to set the ``Location`header on success.

    Returns
    -------
    FooResponse
        The created Foo.

    Raises
    ------
    pydantic.ValidationError
        Raised by FastAPI/Pydantic on invalid payload (returned as 422).

    Side Effects (Optional)
    -----------------------
    - Persists the new Foo via the configured repository.
    - Sets a ``Location`header to ``/api/v1/foo``.

    Examples
    --------
    >>> # via HTTP (curl)
    >>> # curl -s -X POST "http://127.0.0.1:${BACKEND_PORT}/api/${API_VERSION}/foo" \\
    ... #   -H "content-type: application/json" \\
    ... #   -d '{"name":"Kratos"}'
    """
    foo = svc.create(name=payload.name)
    # Demonstrate setting a Location header as per API Contract
    response.headers["Location"] = "/api/v1/foo"
    return FooResponse(name=foo.name, length=foo.length())


@router.get(
    "",
    response_model=FooList,
    status_code=status.HTTP_200_OK,
)
async def list_foos(
    svc: Annotated[FooService, Depends(get_foo_service)],
) -> FooList:
    """
    List previously created Foos.

    Parameters
    ----------
    svc : FooService
        Injected service (via DI `Depends(get_foo_service)`).

    Returns
    -------
    FooList
        A container of FooResponse items.

    Examples
    --------
    >>> # via HTTP (curl)
    >>> # curl -s "http://127.0.0.1:${BACKEND_PORT}/api/${API_VERSION}/foo"
    """
    items = [FooResponse(name=f.name, length=f.length()) for f in svc.list()]
    return FooList(items=items)
```

***

## 15) Maintainer Checklist

*   Update the **module/package docstring** when public entry points change.
*   Keep **`__all__`** aligned with **Public API**; avoid re-exporting internals.
*   Prefer **absolute imports** inside packages; avoid heavy side effects at import time.
*   Consider **lazy imports** for heavy/rare APIs to reduce cold-start time.
*   Ensure examples are **runnable** (doctest-friendly when practical).
*   Capture **contracts** (pre/postconditions), **security**, and **performance** where it helps consumers.

***
