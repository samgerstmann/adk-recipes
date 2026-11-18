SHELL := /bin/bash
SKILL_NAME := fsi-small-business-loan
PROJECT_ID ?= $(GOOGLE_CLOUD_PROJECT)
PORT ?= 8765

# Boolean config flags to toggle skill surfacing personas
SURFACE_GEMINI_SKILL ?= true
SURFACE_AGENTS_SKILL ?= true

.PHONY: surface install-gemini-skill install-agents-skill install-skill-local test setup demo

# ==============================================================================
# Surface Skill Personas via Boolean Config
# Usage: make surface [SURFACE_GEMINI_SKILL=true|false] [SURFACE_AGENTS_SKILL=true|false]
# ==============================================================================

surface:
	@if [ "$(SURFACE_GEMINI_SKILL)" = "true" ]; then $(MAKE) install-gemini-skill; fi
	@if [ "$(SURFACE_AGENTS_SKILL)" = "true" ]; then $(MAKE) install-agents-skill; fi

install-gemini-skill:
	@mkdir -p "$(HOME)/.gemini/skills"
	@ln -sfn "$(CURDIR)" "$(HOME)/.gemini/skills/$(SKILL_NAME)"
	@echo "✅ Installed $(SKILL_NAME) into ~/.gemini/skills/$(SKILL_NAME)"

install-agents-skill:
	@mkdir -p "$(HOME)/.agents/skills"
	@ln -sfn "$(CURDIR)" "$(HOME)/.agents/skills/$(SKILL_NAME)"
	@echo "✅ Installed $(SKILL_NAME) into ~/.agents/skills/$(SKILL_NAME)"

install-skill-local: install-gemini-skill install-agents-skill

# Offline unit + runnability tests (no cloud calls).
test:
	uv run pytest

# Quick start from the skill dir: temporary design-spec -> .env.
setup:
	@test -n "$(PROJECT_ID)" || { echo "Error: Set PROJECT_ID or GOOGLE_CLOUD_PROJECT"; exit 1; }
	@printf -- "---\ngcp_project_id: $(PROJECT_ID)\nstate_backend: memory\n---\n" > design-spec.md
	@trap 'rm -f design-spec.md' EXIT; uv run python scripts/setup.py --config design-spec.md

# Launch the demo UI from the skill dir (after `make setup`).
demo:
	uv run adk web "$(CURDIR)/scripts" --port $(PORT)
