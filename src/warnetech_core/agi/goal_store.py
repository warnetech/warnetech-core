"""Persistent goal and task graph storage with SQLite backend.

Maintains hierarchical goals, sub-tasks, checkpoints, and reflection insights
across daemon restarts. Supports cycle detection, dependency resolution,
priority queuing, and deterministic checkpoint recovery.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field

log = logging.getLogger("warnetech.agi.goal_store")


class GoalStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass
class Goal:
    id: str
    description: str
    status: GoalStatus = GoalStatus.PENDING
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class TaskNode:
    id: str
    goal_id: str
    title: str
    description: str = ""
    status: str = "pending"
    priority: int = 0  # 0=low, 1=medium, 2=high
    depends_on: list[str] = Field(default_factory=list)
    tool_hint: str | None = None
    estimated_seconds: float | None = None
    result: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


@dataclass
class Checkpoint:
    id: str
    goal_id: str
    task_id: str
    state: dict[str, Any]
    created_at: float = Field(default_factory=time.time)


@dataclass
class Reflection:
    id: str
    goal_id: str
    task_id: str
    aspect: str
    severity: str
    description: str
    heuristic: str | None = None
    created_at: float = Field(default_factory=time.time)


class GoalStore:
    """SQLite-backed persistent goal and task graph store.

    Provides:
    - Cycle detection via topological sort (O(V+E))
    - Dependency resolution and ready-task queuing
    - Priority-based task scheduling
    - Checkpoints for crash recovery
    - Reflection insights for future task planning
    - Multi-step goal persistence across daemon restarts
    """

    def __init__(self, db_path: Path | str = "goals.db") -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        cur = conn.cursor()

        # Goals table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                success_criteria TEXT,
                constraints TEXT,
                metadata TEXT
            )
        """)

        # Tasks table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER DEFAULT 0,
                depends_on TEXT,
                tool_hint TEXT,
                estimated_seconds REAL,
                result TEXT,
                metadata TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (goal_id) REFERENCES goals(id)
            )
        """)

        # Checkpoints table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (goal_id) REFERENCES goals(id),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)

        # Reflections table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reflections (
                id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                aspect TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT NOT NULL,
                heuristic TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (goal_id) REFERENCES goals(id),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)

        conn.commit()

    async def create_goal(
        self,
        goal_id: str,
        description: str,
        *,
        success_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Goal:
        """Create a new goal and store it."""
        now = time.time()
        conn = self._get_conn()
        cur = conn.cursor()

        criteria = json.dumps(success_criteria or [])
        const = json.dumps(constraints or [])
        meta = json.dumps(metadata or {})

        cur.execute(
            """
            INSERT INTO goals (id, description, status, created_at, updated_at,
                             success_criteria, constraints, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (goal_id, description, GoalStatus.PENDING.value, now, now, criteria, const, meta),
        )
        conn.commit()

        return Goal(
            id=goal_id,
            description=description,
            status=GoalStatus.PENDING,
            created_at=now,
            updated_at=now,
            success_criteria=success_criteria or [],
            constraints=constraints or [],
            metadata=metadata or {},
        )

    async def add_task(
        self,
        task_id: str,
        goal_id: str,
        title: str,
        *,
        description: str = "",
        priority: int = 0,
        depends_on: list[str] | None = None,
        tool_hint: str | None = None,
        estimated_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskNode:
        """Add a task to a goal. Detects cycles before inserting."""
        # Check for cycles
        new_deps = depends_on or []
        if self._would_create_cycle(goal_id, task_id, new_deps):
            raise ValueError(f"Task {task_id} would create a cycle in goal {goal_id}")

        now = time.time()
        conn = self._get_conn()
        cur = conn.cursor()

        deps = json.dumps(new_deps)
        meta = json.dumps(metadata or {})

        cur.execute(
            """
            INSERT INTO tasks (id, goal_id, title, description, status, priority,
                             depends_on, tool_hint, estimated_seconds, metadata,
                             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                goal_id,
                title,
                description,
                "pending",
                priority,
                deps,
                tool_hint,
                estimated_seconds,
                meta,
                now,
                now,
            ),
        )
        conn.commit()

        return TaskNode(
            id=task_id,
            goal_id=goal_id,
            title=title,
            description=description,
            priority=priority,
            depends_on=new_deps,
            tool_hint=tool_hint,
            estimated_seconds=estimated_seconds,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )

    def _would_create_cycle(self, goal_id: str, new_task_id: str, deps: list[str]) -> bool:
        """Topological sort to detect cycles (O(V+E))."""
        conn = self._get_conn()
        cur = conn.cursor()

        # Build adjacency list
        cur.execute("SELECT id, depends_on FROM tasks WHERE goal_id = ?", (goal_id,))
        graph: dict[str, set[str]] = {}
        for row in cur.fetchall():
            task_id = row["id"]
            task_deps = json.loads(row["depends_on"] or "[]")
            if task_id not in graph:
                graph[task_id] = set()
            for dep in task_deps:
                graph.setdefault(dep, set()).add(task_id)

        # Add the new task
        if new_task_id not in graph:
            graph[new_task_id] = set()
        for dep in deps:
            graph.setdefault(dep, set()).add(new_task_id)

        # Kahn's algorithm for cycle detection
        in_degree = {task: 0 for task in graph}
        for task in graph:
            for neighbor in graph[task]:
                in_degree[neighbor] = in_degree.get(neighbor, 0) + 1

        queue = [task for task in graph if in_degree[task] == 0]
        processed = 0

        while queue:
            node = queue.pop(0)
            processed += 1
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return processed != len(graph)

    async def update_task_status(
        self, task_id: str, status: str, result: str | None = None
    ) -> None:
        """Update task status and optionally store result."""
        conn = self._get_conn()
        cur = conn.cursor()
        now = time.time()

        cur.execute(
            "UPDATE tasks SET status = ?, result = ?, updated_at = ? WHERE id = ?",
            (status, result, now, task_id),
        )
        conn.commit()

    async def get_ready_tasks(self, goal_id: str) -> list[TaskNode]:
        """Get all tasks ready to execute (dependencies satisfied, pending status)."""
        conn = self._get_conn()
        cur = conn.cursor()

        # Get all completed task IDs
        cur.execute("SELECT id FROM tasks WHERE goal_id = ? AND status = 'completed'", (goal_id,))
        completed = {row["id"] for row in cur.fetchall()}

        # Get all pending tasks
        cur.execute(
            "SELECT * FROM tasks WHERE goal_id = ? AND status = 'pending' ORDER BY priority DESC",
            (goal_id,),
        )

        ready = []
        for row in cur.fetchall():
            deps = json.loads(row["depends_on"] or "[]")
            if all(dep in completed for dep in deps):
                ready.append(
                    TaskNode(
                        id=row["id"],
                        goal_id=row["goal_id"],
                        title=row["title"],
                        description=row["description"] or "",
                        status=row["status"],
                        priority=row["priority"],
                        depends_on=deps,
                        tool_hint=row["tool_hint"],
                        estimated_seconds=row["estimated_seconds"],
                        result=row["result"],
                        metadata=json.loads(row["metadata"] or "{}"),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )

        return ready

    async def save_checkpoint(self, goal_id: str, task_id: str, state: dict[str, Any]) -> str:
        """Save a checkpoint for crash recovery."""
        checkpoint_id = f"cp_{time.time_ns()}"
        conn = self._get_conn()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO checkpoints (id, goal_id, task_id, state, created_at) VALUES (?, ?, ?, ?, ?)",
            (checkpoint_id, goal_id, task_id, json.dumps(state), time.time()),
        )
        conn.commit()

        return checkpoint_id

    async def get_latest_checkpoint(self, goal_id: str, task_id: str) -> dict[str, Any] | None:
        """Get the most recent checkpoint for recovery."""
        conn = self._get_conn()
        cur = conn.cursor()

        cur.execute(
            """SELECT state FROM checkpoints
               WHERE goal_id = ? AND task_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (goal_id, task_id),
        )
        row = cur.fetchone()
        if row:
            return json.loads(row["state"])
        return None

    async def store_reflection(
        self,
        goal_id: str,
        task_id: str,
        aspect: str,
        severity: str,
        description: str,
        heuristic: str | None = None,
    ) -> str:
        """Store a reflection insight for future task planning."""
        reflection_id = f"refl_{time.time_ns()}"
        conn = self._get_conn()
        cur = conn.cursor()

        cur.execute(
            """INSERT INTO reflections
               (id, goal_id, task_id, aspect, severity, description, heuristic, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reflection_id,
                goal_id,
                task_id,
                aspect,
                severity,
                description,
                heuristic,
                time.time(),
            ),
        )
        conn.commit()

        return reflection_id

    async def get_similar_reflections(self, aspect: str, limit: int = 10) -> list[Reflection]:
        """Get reflections on similar aspects for injection into future planning."""
        conn = self._get_conn()
        cur = conn.cursor()

        cur.execute(
            """SELECT * FROM reflections
               WHERE aspect LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (f"%{aspect}%", limit),
        )

        return [
            Reflection(
                id=row["id"],
                goal_id=row["goal_id"],
                task_id=row["task_id"],
                aspect=row["aspect"],
                severity=row["severity"],
                description=row["description"],
                heuristic=row["heuristic"],
                created_at=row["created_at"],
            )
            for row in cur.fetchall()
        ]

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    async def __aenter__(self) -> GoalStore:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
