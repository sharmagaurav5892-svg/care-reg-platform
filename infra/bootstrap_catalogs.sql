-- One-time bootstrap. Run by a person, once, in the Databricks SQL Editor.
--
-- Everything else (schemas, volume, jobs, tables) is created by the bundle
-- through CI/CD. Catalogs are the exception: in a company they're created by
-- the platform team with their own storage and permissions, before any
-- project deploys into them. This file is that step, written down.

CREATE CATALOG IF NOT EXISTS care_reg_dev
  COMMENT 'CareReg Intelligence Platform, DEV. Public seniors care legislation (BC, ON). Deployed by CI on merge to main.';

CREATE CATALOG IF NOT EXISTS care_reg_prod
  COMMENT 'CareReg Intelligence Platform, PROD. Deployed by CI from release tags after manual approval.';

SHOW CATALOGS LIKE 'care_reg*';
