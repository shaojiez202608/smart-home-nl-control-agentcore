# Third-party licenses

## Components included in this repository
None. The repository contains no vendored third-party code.

## Packages downloaded at build or deploy time
Direct dependencies and their licenses as declared in package metadata (versions resolved in `uv.lock` or pinned
in `src/agent/requirements.txt`). Transitive dependencies are listed in `uv.lock`; generate a full report with
`uvx pip-licenses --from=mixed` before publishing. The agent package bundles these wheels at deploy time and runs
on the managed `PYTHON_3_13` runtime. The optional container path (`src/agent/Dockerfile`) uses the
`public.ecr.aws/docker/library/python:3.13-slim` base image, which has its own licenses.

Nothing here relicenses a third-party component; each package remains under its own license.

### Lambda layer (deployed)

| Package | Version | License |
|---|---|---|
| redis | 8.1.0 | MIT |
| boto3 | 1.43.115 | Apache-2.0 |
| botocore | 1.43.115 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| s3transfer | 0.19.3 | Apache License 2.0 |
| jmespath | 1.1.0 | MIT |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause (dual) |
| six | 1.17.0 | MIT |

### Agent container (deployed)

| Package | Version | License |
|---|---|---|
| strands-agents | 1.61.1 | Apache-2.0 |
| bedrock-agentcore | 1.24.2 | Apache-2.0 |
| aws-opentelemetry-distro | 0.21.0 | Apache-2.0 (read from the built agent image) |
| boto3 | 1.43.115 | Apache-2.0 |
| pydantic | 2.14.1 | MIT |
| pydantic-core | 2.50.1 | MIT |
| mcp | 2.1.1 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| opentelemetry-sdk | 1.45.1 | Apache-2.0 |

### Infrastructure (build time)

| Package | Version | License |
|---|---|---|
| aws-cdk-lib | 2.273.0 | Apache-2.0 |
| constructs | 10.8.1 | Apache-2.0 |
| cdk-nag | 3.0.2 | Apache-2.0 |
| jsii | 1.142.0 | Apache-2.0 |

### Development and scripts (not deployed)

| Package | Version | License |
|---|---|---|
| pytest | 9.1.1 | MIT |
| ruff | 0.17.1 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| awsiotsdk | 1.31.0 | License :: OSI Approved :: Apache Software License |
| awscrt | 0.36.1 | Apache-2.0 |
