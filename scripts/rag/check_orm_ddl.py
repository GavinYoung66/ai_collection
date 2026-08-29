#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§13.2 数据模型双向 CI 校验脚本（DDL ↔ ORM，对齐 03_db_vector_spec【强制9】）

单一事实源：requirements_document/step4_数据与库表设计/01_数据模型与关系库表设计.md
对齐对象：services/rag-service/app/models/orm.py（公共字段来源 common/db/base.py PublicFieldsMixin）

检查维度（零误报设计，WARN 不阻塞）：
  ① 表存在性：DDL rag-service 域表必须存在于 ORM（缺失 FAIL）；ORM 表须在 DDL 或豁免清单
  ② 列存在性：DDL 表每一列必须在 ORM 类（含公共字段 mixin 并入）
  ③ 类型等价：归一化比较（VARCHAR(500)↔String(500)、BIGSERIAL↔BigInteger、
     TIMESTAMP WITH TIME ZONE↔DateTime(timezone=True)、CHAR(1)↔CHAR(1)、TEXT↔Text、UUID↔UUID）
  ④ nullable 单向校验（仅 kb_doc_assets 严格全列；存量表跳过避免 DDL/ORM 风格差异误报）：
     DDL NOT NULL → ORM 显式非空必须成立（缺失 FAIL）；DDL 可空 + ORM 显式 nullable=False → WARN
  ⑤ 索引严格比对（仅 kb_doc_assets 双索引 DDL ↔ ORM __table_args__，任务焦点；存量表 INFO）
  ⑥ CHECK 声明：DDL 侧存在性记录（ORM 无 CHECK 表达不双向比对）；kb_doc_assets 必须含
     asset_type/is_delete 两条 CHECK（文档完整性）

豁免（WARN 不 FAIL）：kb_search_logs 为 append-only 日志表，step4/01 收录范围不含（任务范围精确，
与 V5 迁移 0008 仅收录 kb_doc_assets 一致）。

用法：python scripts/rag/check_orm_ddl.py
退出码：0=通过，1=有违规（仅 FAIL 计分）
"""

import ast
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DDL_PATH = PROJECT_ROOT / "requirements_document" / "step4_数据与库表设计" / "01_数据模型与关系库表设计.md"
ORM_PATH = PROJECT_ROOT / "services" / "rag-service" / "app" / "models" / "orm.py"
MIXIN_PATH = PROJECT_ROOT / "common" / "db" / "base.py"

# DDL 中 rag-service 域表（双向比对范围；kb_search_logs 属豁免日志表，不在此列）
RAG_DOMAIN_TABLES = ("kb_documents", "kb_chunks", "kb_doc_assets", "sensitive_words")
# ORM 存在但 step4/01 未收录的表（append-only 日志表）→ WARN 豁免
EXEMPT_TABLES = {"kb_search_logs"}

# 公共字段（mixin 并入 ORM 解析结果；DDL 侧公共列仅校验存在性+类型，nullable 由 mixin 统一治理）
COMMON_FIELDS = ("id", "create_time", "update_time", "is_delete")

# ---------- 类型归一化 ----------

DDL_TYPE_KEYWORDS = {
    "BIGSERIAL", "UUID", "VARCHAR", "TEXT", "INT", "INTEGER", "BIGINT", "SMALLINT",
    "TIMESTAMP", "TIME", "WITH", "ZONE", "CHAR", "FLOAT", "DOUBLE", "PRECISION",
    "BOOLEAN", "JSONB", "NUMERIC", "DECIMAL", "DATE",
}


# ORM 类型名 → DDL 类型名（双向归一化映射，键统一大写）
ORM_TO_DDL = {
    "STRING": "VARCHAR",
    "TEXT": "TEXT",
    "INTEGER": "INT",
    "BIGINTEGER": "BIGSERIAL",
    "CHAR": "CHAR",
    "DATETIME": "TIMESTAMP",
    "UUID": "UUID",
    "FLOAT": "FLOAT",
    "BOOLEAN": "BOOLEAN",
}


def _norm_type(base: str, length, extra: str = "") -> tuple:
    base = ORM_TO_DDL.get(base.upper(), base.upper())
    if base == "BIGSERIAL":
        return ("bigserial", None)
    if base == "UUID":
        return ("uuid", None)
    if base == "VARCHAR":
        return ("varchar", length)
    if base == "CHAR":
        return ("char", length)
    if base in ("TEXT", "JSONB", "DATE"):
        return (base.lower(), None)
    if base in ("INT", "INTEGER"):
        return ("integer", None)
    if base == "BIGINT":
        return ("bigint", None)
    if base == "SMALLINT":
        return ("smallint", None)
    if base == "TIMESTAMP":
        if "WITH TIME ZONE" in extra:
            return ("timestamptz", None)
        return ("timestamp", None)
    if base == "FLOAT":
        return ("float", None)
    if base == "DOUBLE":  # DOUBLE PRECISION
        return ("float", None)
    if base == "NUMERIC":
        return ("numeric", None)
    if base == "DECIMAL":
        return ("numeric", None)
    if base == "BOOLEAN":
        return ("boolean", None)
    return (base.lower(), length)


# ---------- DDL 解析（SQL 词法提取） ----------

CREATE_TABLE_RE = re.compile(r"CREATE TABLE\s+([A-Za-z_][\w.]*)\s*\((.*?)\);", re.S)
CREATE_INDEX_RE = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(\w+)\s+ON\s+([A-Za-z_][\w.]*)\s*\(([^)]+)\)")
CHECK_RE = re.compile(r"CHECK\s*\(([^)]*)\)")


def parse_ddl(text: str) -> dict:
    """返回 {table: {cols: {col: dict}, indexes: {name: (cols, unique)}, checks: {col: [check]}}}"""
    tables = {}
    for tm in CREATE_TABLE_RE.finditer(text):
        tname = tm.group(1)
        body = tm.group(2)
        cols = {}
        checks = {}
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("--") or line.startswith(("UNIQUE", "PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT")):
                continue
            words = line.split()
            if len(words) < 2:
                continue
            colname = words[0]
            i = 1
            type_parts = []
            while i < len(words) and words[i].split("(")[0].upper() in DDL_TYPE_KEYWORDS:
                type_parts.append(words[i])
                i += 1
            if not type_parts:
                continue
            type_str = " ".join(type_parts)
            tail = " ".join(words[i:])
            m = re.match(r"([A-Z_]+)(?:\(([^)]*)\))?", type_str)
            base = m.group(1)
            length = None
            if m.group(2) is not None and base in ("VARCHAR", "CHAR", "NUMERIC", "DECIMAL"):
                try:  # NUMERIC(12,2) 精度串不参与长度比对
                    length = int(m.group(2))
                except ValueError:
                    length = None
            cols[colname] = {
                "type": _norm_type(base, length, type_str),
                "not_null": "NOT NULL" in tail,
                "pk": "PRIMARY KEY" in tail or "PRIMARY KEY" in line,
                "unique": "UNIQUE" in tail,
                "default": re.search(r"DEFAULT\s+([^,]+)$", tail).group(1).strip() if re.search(r"DEFAULT\s+([^,]+)$", tail) else None,
            }
            for cm in CHECK_RE.finditer(tail):
                checks.setdefault(colname, []).append(cm.group(1).strip())
        tables[tname] = {"cols": cols, "indexes": {}, "checks": checks}
        for im in CREATE_INDEX_RE.finditer(text):
            if im.group(2) == tname:
                idx_cols = tuple(c.strip() for c in im.group(3).split(","))
                tables[tname]["indexes"][im.group(1)] = (idx_cols, im.group(0).startswith("CREATE UNIQUE"))
    return tables


# ---------- ORM 解析（AST 提取） ----------

ORM_TYPE_NAMES = {"String", "Text", "Integer", "BigInteger", "CHAR", "DateTime", "UUID", "Float", "Boolean"}


def _annotation_nullable(ann) -> bool | None:
    """按类型注解推断 nullable：Mapped[X] → False；Mapped[X | None] → True；无法识别 → None"""
    if not isinstance(ann, ast.Subscript) or not isinstance(ann.slice, ast.BinOp):
        return None
    if not isinstance(ann.slice.op, ast.BitOr):
        return None
    for el in (ann.slice.left, ann.slice.right):
        if isinstance(el, ast.Constant) and el.value is None:
            return True
    return False


def _orm_col(call: ast.Call) -> dict | None:
    if not isinstance(call.func, ast.Name) or call.func.id not in ("mapped_column", "Column"):
        return None
    args = call.args
    if not args:
        return None
    arg0 = args[0]
    if isinstance(arg0, ast.Name) and arg0.id in ORM_TYPE_NAMES:
        base, length, extra = arg0.id, None, ""
    elif isinstance(arg0, ast.Call) and isinstance(arg0.func, ast.Name) and arg0.func.id in ORM_TYPE_NAMES:
        base, length, extra = arg0.func.id, None, ""
        for a in arg0.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, int):
                length = a.value
        for kw in arg0.keywords:
            if kw.arg == "timezone" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                extra = " WITH TIME ZONE"
    else:
        return None
    kwargs = {}
    for kw in call.keywords:
        if kw.arg in ("nullable", "primary_key", "unique", "index", "autoincrement"):
            if isinstance(kw.value, ast.Constant):
                kwargs[kw.arg] = kw.value.value
    return {"type": _norm_type(base, length, extra), "nullable": kwargs.get("nullable"), "pk": kwargs.get("primary_key", False), "unique": kwargs.get("unique", False)}


def _parse_class_cols(cls: ast.ClassDef) -> dict:
    cols = {}
    for stmt in cls.body:
        colname = None
        call = None
        annotation = None
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            colname, call, annotation = stmt.target.id, stmt.value, stmt.annotation
        elif isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
            colname, call = stmt.targets[0].id, stmt.value
        if not colname or colname.startswith("__"):
            continue
        col = _orm_col(call) if isinstance(call, ast.Call) else None
        if col is None:
            continue
        if col["nullable"] is None:
            col["nullable"] = _annotation_nullable(annotation)
        cols[colname] = col
    return cols


def _parse_indexes(cls: ast.ClassDef) -> dict:
    """从 __table_args__ 提取 Index 定义 {name: (cols,)}"""
    indexes = {}
    for stmt in cls.body:
        if not (isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "__table_args__"):
            continue
        value = stmt.value
        items = value.elts if isinstance(value, ast.Tuple) else [value]
        for item in items:
            if not (isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "Index"):
                continue
            idx_name = None
            cols = []
            for a in item.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if idx_name is None:
                        idx_name = a.value
                    else:
                        cols.append(a.value)
            if idx_name:
                indexes[idx_name] = tuple(cols)
    return indexes


def parse_orm(src: str) -> dict:
    """返回 {table: {cols: {col: dict}, indexes: {name: (cols,)}}}"""
    tree = ast.parse(src)
    tables = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        tablename = None
        for stmt in node.body:
            if (isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "__tablename__" and isinstance(stmt.value, ast.Constant)):
                tablename = stmt.value.value
        if not tablename:
            continue
        tables[tablename] = {"cols": _parse_class_cols(node), "indexes": _parse_indexes(node)}
    return tables


def parse_mixin(src: str) -> dict:
    """从 PublicFieldsMixin 提取公共字段并入 ORM 表解析结果"""
    tree = ast.parse(src)
    cols = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "PublicFieldsMixin":
            continue
        cols = _parse_class_cols(node)
    return cols


def _type_matches(ddl_type: tuple, orm_type: tuple) -> bool:
    """类型等价：基名必须一致；长度仅当两端都存在时校验（一端缺失不判 FAIL，防误报）"""
    if ddl_type[0] != orm_type[0]:
        return False
    d_len, o_len = ddl_type[1], orm_type[1]
    if d_len is not None and o_len is not None and d_len != o_len:
        return False
    return True


# ---------- 主流程 ----------

def main() -> int:
    ddl = parse_ddl(DDL_PATH.read_text(encoding="utf-8"))
    orm = parse_orm(ORM_PATH.read_text(encoding="utf-8"))
    mixin = parse_mixin(MIXIN_PATH.read_text(encoding="utf-8"))
    for tbl in orm:
        for col, col_info in mixin.items():
            if col not in orm[tbl]["cols"]:
                orm[tbl]["cols"][col] = col_info

    violations, warns, infos = 0, 0, 0

    print("=== DDL ↔ ORM 双向校验（step4/01 单一事实源）===")
    print(f"DDL 文件: {DDL_PATH.relative_to(PROJECT_ROOT)}")
    print(f"ORM 文件: {ORM_PATH.relative_to(PROJECT_ROOT)}")

    # ① 表存在性（DDL rag 域表 → ORM）
    for tname in RAG_DOMAIN_TABLES:
        if tname not in ddl:
            print(f"WARN: DDL 文档未收录 rag-service 域表 {tname}，跳过（收录范围之外）")
            warns += 1
            continue
        if tname not in orm:
            print(f"VIOLATION: DDL 表 {tname} 在 ORM 中缺失")
            violations += 1

    # ② ORM 表 → DDL（豁免清单 WARN）
    for tname in orm:
        if tname in ddl:
            continue
        if tname in EXEMPT_TABLES:
            print(f"WARN: ORM 表 {tname} 未收录 step4/01（append-only 日志表，任务范围精确）")
            warns += 1
        else:
            print(f"VIOLATION: ORM 表 {tname} 在 DDL 文档中缺失")
            violations += 1

    # ③ 逐表列检查
    for tname in RAG_DOMAIN_TABLES:
        if tname not in ddl or tname not in orm:
            continue
        ddl_tbl, orm_tbl = ddl[tname], orm[tname]
        strict_nullable = tname == "kb_doc_assets"  # 新表全列严格；存量表跳过 nullable 避免误报
        print(f"--- 表 {tname}: 列 {len(ddl_tbl['cols'])} / ORM {len(orm_tbl['cols'])} ---")
        for col, ddl_col in ddl_tbl["cols"].items():
            orm_col = orm_tbl["cols"].get(col)
            if orm_col is None:
                print(f"VIOLATION: {tname}.{col} 在 ORM 中缺失")
                violations += 1
                continue
            if not _type_matches(ddl_col["type"], orm_col["type"]):
                print(f"VIOLATION: {tname}.{col} 类型不一致 DDL={ddl_col['type']} vs ORM={orm_col['type']}")
                violations += 1
            if strict_nullable and col not in COMMON_FIELDS:
                if ddl_col["not_null"] and orm_col["nullable"] is not False:
                    print(f"VIOLATION: {tname}.{col} DDL NOT NULL 但 ORM 可空（nullable={orm_col['nullable']}）")
                    violations += 1
                elif not ddl_col["not_null"] and orm_col["nullable"] is False:
                    print(f"WARN: {tname}.{col} DDL 可空但 ORM 显式非空（收紧提示，不阻塞）")
                    warns += 1
        # 公共字段类型核对（DDL 显式声明 vs ORM/mixin）
        for col in COMMON_FIELDS:
            if col not in ddl_tbl["cols"] or col not in orm_tbl["cols"]:
                continue
            if not _type_matches(ddl_tbl["cols"][col]["type"], orm_tbl["cols"][col]["type"]):
                print(f"VIOLATION: {tname}.{col} 公共字段类型不一致 DDL={ddl_tbl['cols'][col]['type']} vs ORM={orm_tbl['cols'][col]['type']}")
                violations += 1
        # ⑤ 索引严格比对（仅 kb_doc_assets）
        if tname == "kb_doc_assets":
            ddl_idx = ddl_tbl["indexes"]
            orm_idx = orm_tbl["indexes"]
            if set(ddl_idx) != set(orm_idx):
                print(f"VIOLATION: kb_doc_assets 索引不一致 DDL={sorted(ddl_idx)} vs ORM={sorted(orm_idx)}")
                violations += 1
            else:
                for name, (ddl_cols, _unique) in ddl_idx.items():
                    if orm_idx[name] != ddl_cols:
                        print(f"VIOLATION: kb_doc_assets 索引 {name} 列不一致 DDL={ddl_cols} vs ORM={orm_idx[name]}")
                        violations += 1
                print(f"INFO: kb_doc_assets 索引双向一致 {sorted(ddl_idx)}")
                infos += 1
        else:
            if ddl_tbl["indexes"]:
                print(f"INFO: {tname} 索引 {len(ddl_tbl['indexes'])} 条（非焦点表，不严格比对）")
                infos += 1
        # ⑥ CHECK 声明（kb_doc_assets 完整性）
        if tname == "kb_doc_assets":
            check_cols = sorted(ddl_tbl["checks"])
            for need in ("asset_type", "is_delete"):
                if need not in ddl_tbl["checks"]:
                    print(f"VIOLATION: kb_doc_assets 缺 CHECK 约束列 {need}")
                    violations += 1
            print(f"INFO: kb_doc_assets CHECK 声明 {check_cols}")
            infos += 1

    print("")
    if violations > 0:
        print(f"FAIL: 发现 {violations} 处违规（WARN {warns} 条，不阻塞）")
        return 1
    print(f"PASS: DDL ↔ ORM 双向校验通过（0 违规；WARN {warns} 条，INFO {infos} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
