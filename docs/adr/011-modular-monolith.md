# ADR-011 — Modular monolith first

- Status: Accepted (2026-09-27, khóa từ `plan_v2_revised.md` §§2.9, 68).
- Scope: deployment (mọi phase V1).

## Context

Microservice mesh từ ngày một làm phức tạp vận hành vượt giá trị một MVP
chưa đo được.

## Decision

- V1 là **một deployable** (FastAPI process + Postgres + pgvector + object store).
- Ranh giới là **package logic** (`domain/application/routing/providers/adapters/
  control_plane/evaluation/observability`), không phải network boundary.
- Chỉ tách service khi có bằng chứng đo được: scaling độc lập, security boundary khác,
  reliability isolation, ownership khác, background workload nặng, latency profile
  không tương thích, deployment cadence độc lập.

## Consequences

- Không Kafka/K8s/Redis/Celery cho đến khi đo thấy cần (§50).
- Control plane không phải hop đồng bộ trong route request (ADR-003).

## Rejected

- Microservice mỗi module; K8s cho MVP local.

## Verification

- `docker-compose.yml` chỉ 1 service app-side (db); không có mesh nào trong repo.
