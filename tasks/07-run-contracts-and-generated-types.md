# 07 Run Contracts And Generated Types

Status: Done

## Goal

Extend the shared run contract so the web app and backend can describe real workflow execution
without overloading the generic `metadata` field.

## Scope

- Add an explicit workflow configuration object to the shared contracts.
- Model the fields needed for real execution, such as provider-specific model name, token limits,
  and optional runtime overrides.
- Regenerate frontend contract types from the Python source of truth.
- Update API validation and persistence so the new config is stored and returned with runs.
- Add tests for request validation, serialization, and backward-compatible defaults.

## Deliverables

- Updated Pydantic models in `packages/contracts`.
- Regenerated TypeScript types in `apps/web`.
- API support for reading and writing structured workflow config.
- Test coverage for defaulting, validation, and backward compatibility.

## Exit Criteria

- A run can be created with structured workflow configuration instead of free-form metadata only.
- The frontend and backend share the same generated schema for workflow config.
- Existing simple runs continue to work with sensible defaults.

## Notes

- Keep the contract narrow. Add only fields the worker can actually honor in the next milestone.
- Prefer typed config over provider-specific logic leaking into the generic run record.
