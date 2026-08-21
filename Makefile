.PHONY: dev test lint migrate compile install smoke clean

# 启动开发环境
dev:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# 编译依赖锁
compile:
	pip-compile requirements.in -o requirements.lock

# 安装依赖
install:
	pip install -r requirements.lock

# 数据库迁移
migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

# 测试
test:
	pytest --cov=common --cov-report=term-missing --cov-fail-under=85

# 静态扫描
lint:
	ruff check .
	bandit -r common/ services/
	mypy common/

# 冒烟测试
smoke:
	bash scripts/smoke/smoke_all.sh

# 清理
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml down -v
