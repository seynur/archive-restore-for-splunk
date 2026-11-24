APP := archive_restore
VERSION := $(shell awk -F' *= *' '/^version/{print $$2; exit}' $(APP)/default/app.conf)
DIST := dist/$(APP)-$(VERSION).tgz
SPLUNK_SDK := 2.1.1

.PHONY: check test lint fmt package appinspect vendor clean

check: lint test

test:
	python3 -m pytest -q tests

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff check --fix .
	ruff format .

# Re-vendor splunklib (pinned). deprecation dep is only used by splunklib.results (not imported).
vendor:
	rm -rf $(APP)/bin/lib && pip install --no-deps --target $(APP)/bin/lib splunk-sdk==$(SPLUNK_SDK)

# The app folder is exactly what ships; only caches and site config are left out.
package: clean
	mkdir -p dist
	COPYFILE_DISABLE=1 tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.*' \
	  --exclude='$(APP)/local' --exclude='$(APP)/metadata/local.meta' -czf $(DIST) $(APP)
	@echo $(DIST)

appinspect: package
	splunk-appinspect inspect $(DIST) --mode precert

clean:
	rm -rf dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
