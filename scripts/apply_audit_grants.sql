-- ---------------------------------------------------------------------------
-- Layer 2 of audit protection (ADR-010) — database-enforced immutability.
--
-- MySQL's privilege model is ADDITIVE: a table-level grant adds to a
-- database-level grant, it cannot subtract from one. So "deny UPDATE on this
-- one table" is expressed by revoking UPDATE/DELETE for the whole database and
-- re-granting them on every table EXCEPT core_auditevent.
--
-- Run as root AFTER every migration that creates a table: a new table is
-- created WITHOUT the table-level grant and is therefore INSERT-only to the
-- application until this runs again. `manage.py verify_audit_grants` reports
-- both halves — that the audit table is protected AND that no other table was
-- left behind.
--
-- IDEMPOTENT. The REVOKE uses IF EXISTS because after the first run there is
-- no blanket grant left to revoke, and a plain REVOKE fails with error 1141 —
-- aborting the script BEFORE the re-grant loop. That is precisely how nine
-- tables ended up read-only across Sprints 2B and 3: the script was run, it
-- errored on line 1, and nothing said so.
--
-- Usage:
--   mysql --socket=/tmp/mysql_ce84.sock -u root continuing_education \
--     < scripts/apply_audit_grants.sql
-- ---------------------------------------------------------------------------

SET @db   := 'continuing_education';
SET @user := 'ce_app';

-- 1) Remove the blanket UPDATE/DELETE that covers every table.
SET @sql := CONCAT('REVOKE IF EXISTS UPDATE, DELETE ON `', @db, '`.* FROM ',
                   QUOTE(@user), '@''localhost''');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @sql := CONCAT('REVOKE IF EXISTS UPDATE, DELETE ON `', @db, '`.* FROM ',
                   QUOTE(@user), '@''127.0.0.1''');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 2) Re-grant them table by table, skipping the audit table.
DROP PROCEDURE IF EXISTS regrant_except_audit;
DELIMITER //
CREATE PROCEDURE regrant_except_audit()
BEGIN
  DECLARE done INT DEFAULT 0;
  DECLARE tname VARCHAR(128);
  DECLARE cur CURSOR FOR
    SELECT TABLE_NAME FROM information_schema.TABLES
     WHERE TABLE_SCHEMA = 'continuing_education'
       AND TABLE_TYPE = 'BASE TABLE'
       AND TABLE_NAME <> 'core_auditevent';
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;

  OPEN cur;
  read_loop: LOOP
    FETCH cur INTO tname;
    IF done THEN LEAVE read_loop; END IF;

    SET @g := CONCAT('GRANT UPDATE, DELETE ON `continuing_education`.`',
                     tname, '` TO ''ce_app''@''localhost''');
    PREPARE s FROM @g; EXECUTE s; DEALLOCATE PREPARE s;

    SET @g := CONCAT('GRANT UPDATE, DELETE ON `continuing_education`.`',
                     tname, '` TO ''ce_app''@''127.0.0.1''');
    PREPARE s FROM @g; EXECUTE s; DEALLOCATE PREPARE s;
  END LOOP;
  CLOSE cur;
END //
DELIMITER ;

CALL regrant_except_audit();
DROP PROCEDURE regrant_except_audit;
FLUSH PRIVILEGES;
