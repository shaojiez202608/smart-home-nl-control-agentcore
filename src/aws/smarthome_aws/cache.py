"""Amazon ElastiCache (Valkey, serverless) adapter with IAM authentication.

No static password exists anywhere: the Lambda role signs a short-lived
token with SigV4 and the cache verifies it. Connections use TLS.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
import redis
from botocore.model import ServiceId
from botocore.signers import RequestSigner
from redis.credentials import CredentialProvider

TOKEN_TTL_SECONDS = 900


class IamTokenProvider(CredentialProvider):
    def __init__(self, user_id: str, cache_name: str, region: str):
        self.user_id = user_id
        self.cache_name = cache_name
        self.region = region
        session = boto3.session.Session()
        self._signer = RequestSigner(
            ServiceId("elasticache"), region, "elasticache", "v4", session.get_credentials(), session.events
        )
        self._token = ""
        self._expires = 0.0

    def get_credentials(self):
        now = time.time()
        if now >= self._expires:
            url = self._signer.generate_presigned_url(
                {
                    "method": "GET",
                    "url": f"https://{self.cache_name}/?Action=connect&User={self.user_id}&ResourceType=ServerlessCache",
                    "body": {},
                    "headers": {},
                    "context": {},
                },
                operation_name="connect",
                expires_in=TOKEN_TTL_SECONDS,
                region_name=self.region,
            )
            self._token = url.removeprefix("https://")
            self._expires = now + TOKEN_TTL_SECONDS - 60
        return self.user_id, self._token


class ValkeyCache:
    def __init__(self, client: redis.Redis):
        self.r = client

    @classmethod
    def from_env(cls) -> ValkeyCache:
        provider = IamTokenProvider(
            user_id=os.environ["CACHE_USER_ID"],
            cache_name=os.environ["CACHE_NAME"],
            region=os.environ["AWS_REGION"],
        )
        client = redis.Redis(
            host=os.environ["CACHE_ENDPOINT"],
            port=int(os.environ.get("CACHE_PORT", "6379")),
            ssl=True,
            credential_provider=provider,
            socket_connect_timeout=2,
            socket_timeout=1,
            decode_responses=True,
            health_check_interval=30,
        )
        return cls(client)

    def get_json(self, key: str) -> Any | None:
        v = self.r.get(key)
        return json.loads(v) if v is not None else None

    def mget_json(self, keys: list[str]) -> list[Any | None]:
        if not keys:
            return []
        # Serverless caches run in cluster mode; keys may live on different
        # slots, so fetch them in a pipeline rather than one MGET.
        pipe = self.r.pipeline(transaction=False)
        for k in keys:
            pipe.get(k)
        return [json.loads(v) if v is not None else None for v in pipe.execute()]

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)

    def pop_json(self, key: str) -> Any | None:
        v = self.r.getdel(key)
        return json.loads(v) if v is not None else None
