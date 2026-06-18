# Changelog

## [0.3.1](https://github.com/thevivotran/carange/compare/v0.3.0...v0.3.1) (2026-06-18)


### Bug Fixes

* **telegram:** force IPv4 to prevent ENETUNREACH in k8s flannel pods ([#44](https://github.com/thevivotran/carange/issues/44)) ([4080684](https://github.com/thevivotran/carange/commit/40806849fc83ce0d8a89ee52f698cbacd30bcac7))

## [0.3.0](https://github.com/thevivotran/carange/compare/v0.2.1...v0.3.0) (2026-06-18)


### Features

* **budget:** surface budget awareness in transactions and Telegram ([#42](https://github.com/thevivotran/carange/issues/42)) ([907f2f7](https://github.com/thevivotran/carange/commit/907f2f763453bc4ce0962401583216a335fe5186))
* **ocr:** upgrade to PaddleOCR 3.x + add VN bank parsers + AI fallback loop ([e537c7e](https://github.com/thevivotran/carange/commit/e537c7e836bf7dcb35ae0ced13ac33819c7fe7ac))


### Bug Fixes

* **ci:** use python:3.12-slim base image for ocr-worker Docker build ([94b5425](https://github.com/thevivotran/carange/commit/94b542569dc28e606d2c86fb0f7b422c54d62904))
* **security:** add human approval gate for AI-generated parsers (Option A) ([2b61945](https://github.com/thevivotran/carange/commit/2b61945bc8cb305bcf79942b77260da644f1663b))

## [0.2.1](https://github.com/thevivotran/carange/compare/v0.2.0...v0.2.1) (2026-06-17)


### Bug Fixes

* **notify:** commit tx_ingested atomically with transaction to fix ordering race ([83aa0a1](https://github.com/thevivotran/carange/commit/83aa0a134f76640fc02aa6f19ee9ee0be91a3f58))

## [0.2.0](https://github.com/thevivotran/carange/compare/v0.1.1...v0.2.0) (2026-06-16)


### Features

* **notify:** replace fire-and-forget Telegram with durable event queue ([#39](https://github.com/thevivotran/carange/issues/39)) ([e913f36](https://github.com/thevivotran/carange/commit/e913f36df4902a7ec402f887733f5d64a8ca28ad))


### Bug Fixes

* **notify:** make Telegram sends non-blocking to prevent request stalls ([022400d](https://github.com/thevivotran/carange/commit/022400df579ba1212b346ed81997126ee6bbd11f))

## [0.1.1](https://github.com/thevivotran/carange/compare/v0.1.0...v0.1.1) (2026-06-16)


### Bug Fixes

* **budget:** make progress bar fill consistently reflect % used ([1d88bba](https://github.com/thevivotran/carange/commit/1d88bba757219d98edba868479fa5e80b4d10cdc))
* **notify:** fire Telegram advance ping when converting normal → advance ([a3ea7ad](https://github.com/thevivotran/carange/commit/a3ea7ad664de2748d2f1959a9a049370518a85ce))

## Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project follows [Semantic Versioning](https://semver.org/). Releases and
their changelog entries below are generated automatically by
[release-please](https://github.com/googleapis/release-please) from
[Conventional Commits](https://www.conventionalcommits.org/) merged into `main`.

---

Earlier history is available via `git log` and the merged pull requests on GitHub.
