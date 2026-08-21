-- PostgreSQL 初始化脚本
-- 创建扩展 + dw schema

-- 创建 uuid 扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 创建 dw schema（数据中台分层）
CREATE SCHEMA IF NOT EXISTS dw;

-- 验证
SELECT schema_name FROM information_schema.schemata WHERE schema_name IN ('public', 'dw');
