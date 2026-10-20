# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-21
### Added
- Natural-language control path: preload, single model call on AgentCore Runtime, validation, sensitive-action
  policy, parallel dispatch through the existing platform API, reply from acceptance results.
- Asynchronous state path over AWS IoT Core with command receipts.
- AgentCore Memory with deterministic preference governance; AgentCore Gateway (OpenAPI target, API-key provider)
  with a default-deny Cedar policy engine; AgentCore Identity (OAuth2 M2M); runtime resource policy.
- Synthetic evaluation set (mock and real-model modes), unit, adapter and config tests, end-to-end script,
  preflight and leftover-check scripts; cdk-nag clean synth.
