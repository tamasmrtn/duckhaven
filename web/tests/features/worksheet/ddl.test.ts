import { describe, it, expect } from "vitest";
import { isDdl } from "@/features/worksheet/ddl";

describe("isDdl", () => {
  it.each([
    "CREATE TABLE t (id int)",
    "alter table t add column x int",
    "  DROP VIEW v",
    "Create Schema s",
  ])("is true for DDL: %s", (sql) => {
    expect(isDdl(sql)).toBe(true);
  });

  it.each([
    "SELECT 1",
    "with x as (select 1) select * from x",
    "INSERT INTO t VALUES (1)",
    "update t set x = 1",
    "delete from t",
  ])("is false for non-DDL: %s", (sql) => {
    expect(isDdl(sql)).toBe(false);
  });
});
