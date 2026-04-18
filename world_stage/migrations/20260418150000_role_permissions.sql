BEGIN;

-- Move role permissions from hard-coded Python to the account_role lookup
-- table so the rules live with the data. The mapping mirrors what the old
-- UserPermissions enum enforced: editor/admin/owner can edit; admin/owner
-- can view restricted data.
ALTER TABLE account_role ADD COLUMN can_edit boolean NOT NULL DEFAULT false;
ALTER TABLE account_role ADD COLUMN can_view_restricted boolean NOT NULL DEFAULT false;

UPDATE account_role SET can_edit = true WHERE name IN ('editor', 'admin', 'owner');
UPDATE account_role SET can_view_restricted = true WHERE name IN ('admin', 'owner');

COMMIT;
