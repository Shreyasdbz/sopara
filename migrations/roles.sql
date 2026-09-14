-- Declarative production grants. The migration creates NOLOGIN roles so a blank
-- PostgreSQL instance can prove the policy without provisioning cloud identities.
-- Cloud SQL IAM principals are granted these roles only by reviewed deployment SQL.
CREATE ROLE schema_owner NOLOGIN;
CREATE ROLE web_command NOLOGIN;
CREATE ROLE live_engine NOLOGIN;
CREATE ROLE replay_engine NOLOGIN;
CREATE ROLE reconciler NOLOGIN;
CREATE ROLE model_importer NOLOGIN;
CREATE ROLE readonly_owner NOLOGIN;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA sopara FROM PUBLIC;

GRANT USAGE ON SCHEMA sopara TO web_command, live_engine, replay_engine,
  reconciler, model_importer, readonly_owner;
GRANT SELECT ON ALL TABLES IN SCHEMA sopara TO readonly_owner;
GRANT SELECT ON sopara.aggregate_projection, sopara.outbox_event TO web_command;
GRANT SELECT ON sopara.market_object, sopara.dataset_manifest,
  sopara.domain_event TO reconciler;
GRANT INSERT ON sopara.domain_event, sopara.outbox_event,
  sopara.audit_event TO live_engine, replay_engine;
GRANT SELECT, INSERT, UPDATE ON sopara.window_lease,
  sopara.aggregate_projection TO live_engine;
GRANT INSERT ON sopara.model_review TO model_importer;

REVOKE UPDATE, DELETE ON sopara.domain_event, sopara.audit_event FROM PUBLIC,
  web_command, live_engine, replay_engine, reconciler, model_importer,
  readonly_owner;
