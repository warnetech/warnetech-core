# Warnetech-Core AGI Expansion Roadmap

## Executive Summary

The next stage of development for `warnetech/warnetech-core` should focus on transforming the AGI layer from a collection of intelligent components into a structured autonomous reasoning system. Rather than simply adding more agents, prompts, or AI wrappers, the platform should gain stronger capabilities in planning, memory integrity, self-reflection, world modelling, and multi-agent collaboration.

The following roadmap outlines the highest-value additions that would significantly increase AGI capability while remaining aligned with the existing Warnetech architecture.

## 1. AGI Capability Registry

### Purpose

Allow the AGI to dynamically understand what functions, tools, and services are available without hardcoded knowledge.

### Benefits

- Dynamic capability discovery
- Autonomous planning
- Tool selection
- Self-description
- Future plugin ecosystem support
- Runtime feature management

### Example Structure

```json
{
  "capability": "memory_reconstruction",
  "version": "1.0.0",
  "description": "Rebuilds long-term memory structures",
  "inputs": [
    "ghost_id",
    "memory_range"
  ],
  "outputs": [
    "reconstruction_plan"
  ],
  "dependencies": [
    "retention_engine",
    "vector_store",
    "control_plane"
  ]
}
```

### Suggested Directory

```text
agi/
└── capabilities/
    ├── planner.json
    ├── memory.json
    ├── reflection.json
    └── reconstruction.json
```

## 2. Goal Decomposition Engine

### Purpose

Convert high-level objectives into executable task graphs.

Current AI systems often perform isolated actions. An AGI requires the ability to break large goals into smaller executable steps automatically.

### Example

User Goal:

```text
Reconstruct Ghost Alpha
```

Generated Plan:

```yaml
goal: reconstruct_ghost_alpha

tasks:
  - collect_memories
  - identify_timeline
  - detect_gaps
  - fill_gaps
  - validate_reconstruction
  - assign_confidence
  - generate_report
```

### Proposed Components

```python
class GoalPlanner:
    def decompose(goal):
        pass

class TaskGraph:
    pass

class DependencyResolver:
    pass
```

### Benefits

- Autonomous operation
- Reduced manual orchestration
- Better scalability
- Complex mission execution

## 3. Memory Confidence Framework

### Purpose

Every memory stored or reconstructed should be associated with a confidence score.

Without confidence tracking, reconstructed memories become difficult to trust.

### Example

```json
{
  "memory_id": "abc123",
  "confidence": 0.94,
  "sources": 7,
  "contradictions": 0,
  "time_validated": true
}
```

### Confidence Factors

**Positive signals**

- Multiple supporting sources
- Matching timelines
- Verified event records
- Historical agreement

**Negative signals**

- Contradictory memories
- Missing records
- Incomplete timelines
- Corrupt source data

### Benefits

- Hallucination prevention
- Memory integrity
- Trust scoring
- AGI self-validation

## 4. Persistent Agent State System

### Purpose

Allow AGI processes to survive restarts and continue previous work.

### Directory Structure

```text
agent_state/
├── goals.json
├── beliefs.json
├── plans.json
├── memories.json
├── capabilities.json
└── reflections.json
```

### Stored Information

**Goals**

```json
{
  "goal": "reconstruct_alpha",
  "status": "in_progress"
}
```

**Plans**

```json
{
  "plan_id": "plan_001",
  "completion": 73
}
```

**Beliefs**

```json
{
  "ghost_exists": true,
  "confidence": 0.91
}
```

### Benefits

- Long-running intelligence
- Restart recovery
- Continuous learning
- Agent evolution

## 5. Reflection and Self-Critique Engine

### Purpose

Enable the AGI to review its own actions and outcomes.

Most systems perform actions but do not evaluate their effectiveness.

### Workflow

```text
PLAN
 ↓
EXECUTE
 ↓
REFLECT
 ↓
LEARN
 ↓
IMPROVE
```

### Example

```python
result = executor.run(plan)

reflection = reflector.review(
    goal=goal,
    plan=plan,
    result=result
)
```

### Reflection Output

```json
{
  "success": true,
  "confidence": 0.89,
  "issues_detected": [],
  "recommended_improvements": [
    "increase source coverage"
  ]
}
```

### Benefits

- Continuous improvement
- Error detection
- Autonomous optimisation
- Reduced failure rates

## 6. World Model Knowledge Graph

### Purpose

Move from storing memories to understanding relationships.

A memory database alone is not AGI. A knowledge graph creates understanding.

### Example Relationships

```text
Ghost Alpha
    │
    controls
    │
Retention Service

Retention Service
    │
    depends_on
    │
Memory Database
```

### Example Model

```json
{
  "entity": "GhostAlpha",
  "relationship": "controls",
  "target": "RetentionService"
}
```

### Suggested Structure

```text
world_model/
├── entities/
├── relationships/
├── observations/
└── predictions/
```

### Benefits

- Reasoning
- Relationship discovery
- Predictive analysis
- Decision support

## 7. Multi-Ghost Coordination Framework

### Purpose

Allow multiple ghost entities to collaborate on problem solving and reconstruction.

This is one of the highest-value future features.

### Components

```python
class GhostCoordinator:
    pass

class ConsensusEngine:
    pass

class ConflictResolver:
    pass

class TimelineMerger:
    pass
```

### Example

Ghost Alpha:

```json
{
  "fact": "Server restarted at 2:00"
}
```

Ghost Beta:

```json
{
  "fact": "Server restarted at 1:59"
}
```

Consensus output:

```json
{
  "fact": "Server restarted approximately 2:00",
  "supported_by": [
    "ghost_alpha",
    "ghost_beta",
    "system_logs"
  ],
  "confidence": 0.95
}
```

### Features

- Timeline merging
- Consensus generation
- Conflict resolution
- Shared reasoning
- Source attribution

## 8. AGI Evaluation Harness

### Purpose

Measure AGI improvement over time.

If performance cannot be measured, it cannot be improved.

### Suggested Command

```shell
warnetech agi-eval run
```

### Evaluation Categories

- **Reasoning:** Can the system solve complex tasks?
- **Planning:** Can the system break goals into actions?
- **Memory:** Can it recall and reconstruct information?
- **Reflection:** Does it identify mistakes?
- **Tool usage:** Can it choose appropriate tools?

### Example Result

```json
{
  "date": "2026-11-09",
  "reasoning_score": 88,
  "planning_score": 93,
  "memory_score": 91,
  "reflection_score": 84,
  "overall_score": 89
}
```

## 9. Autonomous Tool Selection System

### Purpose

Allow the AGI to choose tools dynamically instead of relying on predefined tool chains.

### Current Model

```python
handler.run()
```

### Future Model

```python
best_tool = planner.choose_tool(task)

best_tool.execute()
```

### Example Decision

```yaml
task: reconstruct_memory

candidates:
  - vector_search
  - graph_search
  - replay_engine

winner: graph_search
```

### Benefits

- Adaptability
- Better efficiency
- Reduced manual configuration
- Increased autonomy

## 10. AGI Telemetry Layer

### Purpose

Record every major reasoning and execution event.

### Example Event

```json
{
  "thought_id": "thought_1001",
  "goal": "reconstruct_alpha",
  "selected_plan": "plan_78",
  "confidence": 0.87,
  "execution_time_ms": 223,
  "status": "completed"
}
```

### Suggested Structure

```text
telemetry/
├── thoughts/
├── plans/
├── actions/
├── reflections/
└── metrics/
```

### Integration

Connect telemetry into:

- Security events
- Threat events
- Retention logs
- Audit trails
- Behaviour analysis

## Recommended Priority Order

**Phase 1 (Immediate)**

1. Goal Decomposition Engine
2. Memory Confidence Framework
3. Capability Registry
4. AGI Telemetry

**Phase 2 (Core AGI)**

5. Reflection Engine
6. Persistent Agent State
7. Autonomous Tool Selection

**Phase 3 (Advanced Intelligence)**

8. World Model Knowledge Graph
9. Multi-Ghost Coordination
10. Consensus & Conflict Resolution

**Phase 4 (Validation)**

11. AGI Evaluation Harness
12. Benchmark Testing Framework
13. Long-Term Learning Metrics

## Final Architecture Vision

```text
 +-------------------+
 |     USER GOAL     |
 +---------+---------+
           |
           v
 +-------------------+
 |   Goal Planner    |
 +---------+---------+
           |
           v
 +-------------------+
 | Capability Registry|
 +---------+---------+
           |
           v
 +-------------------+
 |   Tool Selector   |
 +---------+---------+
           |
           v
 +-------------------+
 | Execution Engine  |
 +---------+---------+
           |
           v
 +-------------------+
 | Reflection Engine |
 +---------+---------+
           |
           v
 +-------------------+
 |    World Model    |
 +---------+---------+
           |
           v
 +-------------------+
 | Multi-Ghost Layer |
 +---------+---------+
           |
           v
 +-------------------+
 |  AGI Telemetry    |
 +-------------------+
```

**Conclusion:** The biggest opportunity for `warnetech-core` is not adding more AI models, but building the planning, memory, reflection, knowledge graph, and multi-ghost coordination infrastructure that allows intelligence to persist, reason, validate itself, and improve over time. This foundation would move the project much closer to a true autonomous reasoning platform rather than a collection of independent AI components.

---

## Implementation notes (added on adoption)

- Phase 1 code lives in the existing package `src/warnetech_core/agi/`, not in a new root-level `agi/` directory. `CLAUDE.md` places AGI modules there.
- Example goals in code and docs must be neutral (for example "Analyse operational telemetry") until the domain model for ghost reconstruction is settled. The Ghost Alpha examples above are illustrative only and are not a commitment to build that feature.
- All model calls go through `AIGateway.complete()`, per `CLAUDE.md` rule 7.
