# Third-party licenses

This project does not relicense any third-party component; each component remains under its own license.
Licenses below were read from the package metadata (`License-Expression`, `License` or the license classifier
in each wheel's `METADATA`) of the packages built or installed on 2026-10-21, not from memory.

## Components distributed with this repository
None. The repository contains no vendored third-party code, fonts, images or data. `sample-data/` is synthetic and
part of this project (MIT-0).

## Components downloaded at build or deploy time

Versions are resolved in `uv.lock` or pinned in `src/agent/requirements.txt` and
`infra/smarthome_infra/build.py`. Transitive dependencies are listed in `uv.lock` (development environment) and
resolved by `uv pip install` when the deployment packages are built; generate a full report with
`uvx pip-licenses --from=mixed` before publishing.

### Lambda layer (built by `infra/smarthome_infra/build.py`, deployed)

| Package | Version | License (package metadata) |
|---|---|---|
| redis | 8.1.0 | MIT |
| boto3 | 1.43.115 | Apache-2.0 |
| botocore | 1.43.116 (resolved at build time) | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| s3transfer | 0.19.3 | Apache License 2.0 |
| jmespath | 1.1.0 | MIT |
| python-dateutil | 2.9.0.post0 | Dual License (Apache-2.0 / BSD-3-Clause per its license file) |
| six | 1.17.0 | MIT |

### Agent package (AgentCore Runtime direct code deployment, deployed)

Direct dependencies and notable transitive ones; the built package contains 112 distributions.

| Package | Version | License (package metadata) |
|---|---|---|
| strands-agents | 1.61.1 | Apache-2.0 |
| bedrock-agentcore | 1.24.2 | Apache-2.0 |
| aws-opentelemetry-distro | 0.21.0 | Apache-2.0 |
| boto3 | 1.43.115 | Apache-2.0 |
| botocore | 1.43.116 | Apache-2.0 |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| mcp | 1.30.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| opentelemetry-api | 1.44.0 | Apache-2.0 |
| opentelemetry-sdk | 1.44.0 | Apache-2.0 |

The optional container path (`src/agent/Dockerfile`, not used by the default deployment) uses the
`public.ecr.aws/docker/library/python:3.13-slim` base image, which carries its own licenses.

### Infrastructure (build time, not deployed as code)

| Package | Version | License (package metadata) |
|---|---|---|
| aws-cdk-lib | 2.273.0 | Apache-2.0 |
| constructs | 10.8.1 | Apache-2.0 |
| cdk-nag | 3.0.2 | Apache-2.0 |
| jsii | 1.142.0 | Apache-2.0 |
| AWS CDK CLI (`aws-cdk` on npm, run with `npx`) | 2.1146.0 | Apache-2.0 |

### Development and scripts (not deployed)

| Package | Version | License (package metadata) |
|---|---|---|
| pytest | 9.1.1 | MIT |
| ruff | 0.17.1 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| awsiotsdk | 1.31.0 | Apache Software License (classifier) |
| awscrt | 0.36.1 | Apache-2.0 |
