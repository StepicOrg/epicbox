REF ?= $(shell git symbolic-ref --short -q HEAD)
IMAGE_NAME ?= stepic/$(shell basename $(PWD))
IMAGE_TAG ?= $(REF)
LAST_COMMIT_HASH := $(shell git log $(REF) -1 --format=%h)
ARCHIVE_FORMAT = tar.gz
ARCHIVE_FILE = app.tar.gz

help:
	@echo "Please use 'make <target>' where <target> is one of"
	@echo "  docker         to build the project container and push it to registry"

archive:
	@git archive --format $(ARCHIVE_FORMAT) -o $(ARCHIVE_FILE) $(REF)

clean:
	@rm -f $(ARCHIVE_FILE)

docker-build: archive
	docker build -t "$(IMAGE_NAME):$(LAST_COMMIT_HASH)" .
	docker tag -f "$(IMAGE_NAME):$(LAST_COMMIT_HASH)" "$(IMAGE_NAME):$(IMAGE_TAG)"

docker-push:
	docker push "$(IMAGE_NAME):$(LAST_COMMIT_HASH)"
	docker push "$(IMAGE_NAME):$(IMAGE_TAG)"

docker: docker-build docker-push clean
