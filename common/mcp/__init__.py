"""MCP 调用公共能力包。

``reliability``（MCP 统一调用编排：错误分级 + 按 server 熔断）在后续批次落地，
本批次仅建包，避免循环依赖与半成品模块被误 import。
"""
