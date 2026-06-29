---
tags: [domain, workflow]
---

# Workflow System

Multi-stage gating layer over the [[Order Lifecycle]].

## Concepts
- **Stages** (`WorkflowStage`) — Enquiry → Lead → Sale → Manufacturing → Fit.
- **Tasks** (`WorkflowTask`) — checkboxes, attachments, radio, dropdown, decision matrices within a stage.
- **Completion** (`TaskCompletion`) — records when a task is done.
- **Gates** — prerequisites (e.g. design check must pass before ordering) via `OrderValidationRequest`.
- **Progress** (`OrderWorkflowProgress`) — tracks where each order sits.
- **Dates** (`WorkflowStageDate`) — expected vs actual stage dates.

## Where it lives in the UI (per repo memory)
- The standalone workflow page was **removed** (June 2026). Workflow editing now lives in the
  **Workflow Management modal** (`#workflowModal`) on the sale/order detail page.
- Modal HTML + management JS in `sale_detail.html` (outside `.detail-tab-panel` divs).
- `openWorkflowModal()` / `selectWorkflowStage()` in `partials/order_tab_content.html`.
- Accordion: phases → stage rows → tasks. Per-stage kebab actions: Set Current, Edit, Add Task, Move, Delete.
- Backend endpoints (all `@login_required`): `save_workflow_stage`, `get_workflow_stage`,
  `get_stage_orders`, `delete_workflow_stage`, `move_workflow_stage`, `save_workflow_task`, `delete_workflow_task`.

> See repo memory `workflow-management` for the full detail of the modal refactor.

## Related
- [[Order Lifecycle]]
- [[Data Models]]
