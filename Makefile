CONTEXT = p1
DASHBOARD_HOST ?= energy.example.ts.net

# One-time setup: docker context create p1 --docker "host=ssh://docker.host.local"
# Set your hostname: export DASHBOARD_HOST=energy.your-tailnet.ts.net

deploy:
	DASHBOARD_HOST=$(DASHBOARD_HOST) \
	DB_NAME=$(DB_NAME) \
	DB_USER=$(DB_USER) \
	DB_PASSWORD=$(DB_PASSWORD) \
	BATTERY_START=$(BATTERY_START) \
	docker --context $(CONTEXT) compose up --build -d

down:
	docker --context $(CONTEXT) compose down

logs:
	docker --context $(CONTEXT) compose logs -f

restart:
	docker --context $(CONTEXT) compose restart

.PHONY: deploy down logs restart
